# LibreHardwareMonitor temperature runtime

This folder contains **unmodified official LibreHardwareMonitorLib 0.9.6** runtime
assemblies and their .NET Framework dependencies. It contains no monitoring GUI,
driver installer, driver service or .NET installer.

- Implementation: `runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll` from
  the official [NuGet package](https://www.nuget.org/packages/LibreHardwareMonitorLib/0.9.6).
  Its `ref/` assembly is intentionally not used.
- Host: CPython x64, pythonnet 3.0.5, clr-loader 0.2.7.post0, explicitly selected
  `netfx`. Tested using CPython 3.12.14, Windows x64, .NET Framework 4.8.1.
- Requirement: Windows x64 with **.NET Framework 4.8 or later**. The .NET runtime
  is supplied by Windows/the user, not bundled. No .NET Core/SDK is required.
- `System.Management` uses the .NET Framework GAC assembly. This is the official
  NuGet net462+ framework dependency; the package's netstandard placeholder DLL
  must not be copied over it.
- `packages.lock.json` records upstream package URLs, versions, SHA-256 hashes,
  implementation members and source revisions. `manifest.json` records deployed
  DLL hashes. `tools/prepare_lhm.py --verify-only` verifies without network access.
  `tools/prepare_lhm.py` restores only these pinned build dependencies.

The library's CPU paths use an **already installed PawnIO driver**. This program
does not install or start that driver, prompt for elevation, change security
settings or run LibreHardwareMonitor.exe. CPU temperature may be unavailable
without suitable driver support/permissions. GPU temperature may work through
the graphics vendor's installed driver API without PawnIO. Support depends on
hardware and installed drivers; unavailable readings remain unavailable.

The 0.9.6 source's `Computer.Open` does not install a driver. Its PawnIO module
opens the existing `\\?\GLOBALROOT\Device\PawnIO` device with `OPEN_EXISTING`.
The signed PawnIO modules are embedded in the upstream library; their license
and source notice are retained in `licenses/`. No `PawnIO_setup.exe` is shipped.

For redistribution retain this entire folder, including `licenses/` and
`THIRD-PARTY-NOTICES.txt`. Unmodified upstream sources are linked in the notices
and in the NuGet lock. Directory builds permit replacement of these separate
assemblies by compatible modified builds. Application integrity verification
may be changed for deliberate local source development by updating the runtime
manifest and the dependency lock together and rebuilding from source; do not
present changed assemblies as official ones.
