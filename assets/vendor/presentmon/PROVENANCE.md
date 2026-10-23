# PresentMon console dependency

- Upstream: Intel / GameTechDev, https://github.com/GameTechDev/PresentMon
- Release: **v2.6.0**, published 2026-09-21
- Binary: https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe
- Binary size: 980320 bytes
- SHA-256: `b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af`
- License: MIT; the unmodified upstream notice is in `LICENSE.txt`.
- License source: https://raw.githubusercontent.com/GameTechDev/PresentMon/v2.6.0/LICENSE.txt
- CLI documentation: https://github.com/GameTechDev/PresentMon/blob/v2.6.0/README-ConsoleApplication.md

The app bundles the standalone x64 console executable. It does not install
PresentMon's service, GUI, SDK, or a kernel driver. It does not request elevation.
It starts a hidden ETW capture with its own randomly named session and stops
only that session at shutdown or when FPS is disabled.

`PawpalMetricsGuard.exe` is built from this project's `tools/metrics_guard.cs`
using the Windows .NET Framework 4 compiler. It watches the parent's process
handle and uses `ControlTraceW` to stop this unique session if the parent is
terminated, then waits for PresentMon to exit. It does not stop any other
session or require an installed PresentMon service. .NET Framework 4.x is a
Windows component on supported Windows 10/11 systems.

Displayed FPS uses non-dropped `msBetweenDisplayChange` values of the foreground
application's primary active swap chain, averaged over a two-second window.
Same-name descendant GPU processes are included for Chromium/Electron apps.
Separate swap chains are never summed, and DWM and the pet itself are excluded.
No supported frame events, no permission, or no foreground target yields an
explicit unavailable state rather than monitor Hz or the pet's animation FPS.
