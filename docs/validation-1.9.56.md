# 1.9.56 validation status

- Python regression suite: 137 tests, 132 passed, 5 skipped.
- Frontend Node tests: 12 passed.
- PowerShell stop helper: syntax parsing passed. Mock execution tests skipped because the build host disallows PowerShell script execution.
- PyInstaller executable build succeeded.
- Packaged executable HTTP/upgrade smoke test and HTTPS smoke test could not start: Windows Application Control returned WinError 4551. They did not pass; no policy bypass was attempted.
- No production service was stopped or upgraded on the build host. Upgrade recovery on the affected computer remains unverified.

This is an upgrade-recovery candidate, not a fully end-to-end validated release. Preserve a stopped-service backup of the data directory before trying it. A policy-approved host or administrator-approved code signing/allowlisting is required for execution if Windows blocks the package. Do not disable application control as a workaround.
