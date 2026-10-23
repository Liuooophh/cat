# CPU usage integration findings and validation

2026-10-24. Changes remain local. This report distinguishes code/mocked tests from actual Windows measurements.

## Evidence before the change

The old `CpuMonitor` correctly declared `GetSystemTimes` as three FILETIME pointers returning BOOL. Actual x64 execution confirmed FILETIME is 8 bytes and both 32-bit halves were retained. Its denominator used kernel + user, with idle subtracted once. It was a whole-system non-idle-time measurement on this 20-logical-processor machine, not this application's CPU use. No evidence supported blaming a ctypes truncation bug or an extra CPU-count division.

Five real baseline reads before implementation returned initial None, then approximately 3.13%, 2.92%, 2.04%, 1.25%. An independent concurrent Windows WMI `ProcessorInformation` Utility query gave 9%, 7%, 22%, 7% over nearby windows. The screenshot's 98% versus 4% has **not been reproduced**, and its exact cause has not been established. Different counters, refresh periods and snapshot freshness need to be distinguished; frequency alone is not a proven explanation.

## Implementation

- Added `pet_cpu.py`; `pet_context.CpuMonitor` is a compatibility import, leaving weather code untouched.
- A persistent PDH query uses **PdhAddEnglishCounterW** so English counter names also work on localized Windows.
- Priority is `\Processor Information(_Total)\% Processor Utility`, then the same object's `% Processor Time`, then `\Processor(_Total)\% Processor Time`. Every path is a total, never `\Process(...)` or the current executable.
- Both PDH call return status and counter `CStatus` are validated. The first sample is None; no blocking sleep occurs in `sample()`. Invalid, negative, non-finite or failed data never reuses the previous display value.
- Utility's raw value may legitimately exceed 100; display is capped at 100 and uncapped values remain in diagnostics. Total counters are already normalized, so no extra division by core count is performed.
- The existing time-based fallback is preserved with correct signatures, full-width FILETIME conversion, fresh baselines after API failures, and rejection of backwards/invalid deltas. Genuine 0% after an actual idle interval remains valid. On >64 logical processors, group-limited GetSystemTimes is rejected rather than claimed to be whole-system.
- Both backends initialize once on the first worker call, with the fallback kept warm. `close()` releases PDH's query/counters and is idempotent. The metrics worker closes the monitor in `finally`; the GUI clears stale CPU readings after 3.5 seconds.
- Public additions are `source`, `counter_path`, `last_error`, `status`, `sampled_at`, `diagnostics`, and `close()`; the existing `sample() -> float | None` remains.

## Official sources and version boundary

- Microsoft describes Task Manager's Windows 8-era change to `% Processor Utility` and the distinction from busy-time metrics: <https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/cpu-usage-exceeds-100>.
- However, Windows 11 KB5053656 (26100.3624, March 2025) changed Task Manager's primary CPU workload metric again and moved the previous Utility measure into an optional Details column. The change was a **gradual rollout**, so build/UBR alone cannot reliably prove that feature was enabled: <https://support.microsoft.com/en-au/servicing/os/windows-11/2025/03/march-27-2025-kb5053656-os-build-26100-3624-preview>.
- Actual local versions were OS build **22621.2283**, Taskmgr.exe **22621.2860**. Utility-first behavior is applicable to this older generation; exact Task Manager agreement on newer rollout states has not been verified or promised. Both total metrics remain available in diagnostics. No unsupported automatic version-threshold guess was added.
- English counter APIs: <https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhaddenglishcounterw>.
- Two successful collections with at least a one-second interval are needed for CPU rate counters: <https://learn.microsoft.com/en-us/windows/win32/perfctrs/collecting-performance-data>.
- Counter `CStatus` must be checked separately from API return status: <https://learn.microsoft.com/en-us/windows/win32/perfctrs/checking-pdh-interface-return-values>.
- GetSystemTimes kernel time includes idle and the >64 processor-group limitation: <https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getsystemtimes>.
- PdhCloseQuery releases the query and its counters: <https://learn.microsoft.com/en-us/windows/win32/api/pdh/nf-pdh-pdhclosequery>.

## Mocked and regression tests

`tests/test_pet_cpu.py`: **24 tests passed**, covering exact Win32 signatures, FILETIME high bits, first-read None, total/English counter paths, no extra core normalization, PDH return/data failures, invalid readings, primary-to-fallback behavior, warm fallback, query release and >64-processor honesty. The existing CPU case in `tests/test_pet_core.py` now explicitly injects a GetSystemTimes fallback. All **23 core tests passed** as well.

These tests use fake counters; in particular the test that supplies Utility 98% and Time 4% is a simulation, not a reproduction of the user's screenshot.

## Real Windows checks

`work/cpu-system-check.py` ran actual `CpuMonitor`, actual direct GetSystemTimes, and an independent PowerShell 7 `Get-Counter` reference. Its complete result is `work/cpu-usage-verification.json`.

- Nine one-second observations on the 20-logical-processor Intel i7-12700H machine; Utility-first collection succeeded without elevation and the initial result was None.
- A single below-normal-priority CPU-bound child was allowed to run for four seconds, keeping the test far below whole-machine saturation. The final run's worker exited with code 0 and consumed 1.328125 seconds of process CPU time. Monitor cleanup and worker exit were confirmed.
- Independent Get-Counter exited with code 0. Eight nearby valid paired samples had a median Utility difference of approximately **1.47 percentage points**, with differing sample boundaries (up to 696 ms offset); this is a cross-consumer comparison, not a claim of exact synchronized readings.
- Across those same samples, PDH Processor Time versus direct GetSystemTimes had a median absolute difference of approximately **0.74 percentage points**.
- Background activity varied, so the short controlled workload did not establish a clean baseline-to-load step. No full-load or 98% scenario was induced or claimed. The purpose was to validate real collection, source selection, independent-counter consistency and cleanup with bounded impact.
- An initial Windows PowerShell 5 reference failed to import its diagnostics module because this workspace inherits PowerShell 7's module paths. The helper was corrected to use the configured PowerShell 7 runtime, after which the reference succeeded. This affects the test helper only; the application does not launch PowerShell for CPU readings.

## Remaining compatibility limits

No real >64-processor, missing/corrupt performance-counter installation, non-English Windows counter UI, or new Task Manager rollout state was available for physical testing. These failure paths have mock coverage or explicit fallback/diagnostics. Sensor temperature and CPU utilization remain separate modules and no driver is needed for this CPU usage implementation.
