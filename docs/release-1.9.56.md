# 1.9.56 Windows upgrade recovery

- Wait for confirmed service STOPPED and application process exit before replacement and health-failure rollback.
- Recover a stuck STOP_PENDING NSSM wrapper only after matching the registered application, wrapper path and current service PID, with no application process remaining.
- Abort on shutdown or previous-executable backup failure. Never terminate unrelated NSSM services.
- Append upgrade diagnostics instead of overwriting earlier entries.
- Preserve existing database and configuration. Standard package does not bundle PostgreSQL or automatically migrate SQLite.

Before upgrading, back up the existing data directory and install to the existing program directory. For the reported installation these are D:\renliang\Cisco and D:\renliang\Cisco\Cisco Network Manager respectively. Do not uninstall first.

Actual upgrade on the affected computer still requires operator verification. Local tests do not stop production services.

PowerShell script execution must be permitted by the administrator's policy. This installer does not bypass execution policy or Windows Application Control. If the stop helper is blocked, installation aborts before replacing the old executable; consult upgrade-diagnostics.log. Automated service-recovery execution tests on the build host were skipped because script execution is restricted; static parsing and installer compilation are checked separately.
