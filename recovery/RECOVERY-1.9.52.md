# 1.9.52 source recovery

Recovered candidate: `v1.9.52-candidate/`.

## Evidence

- The local `network-management-platform-v1.9.51-docker-source.zip` contains 154 files, all matching Git commit `80bb2d0` after newline normalization.
- Sixteen patch operations from the original project session were replayed chronologically into a new directory, from 2026-09-15 05:48:31 UTC through 06:37:30 UTC, before the recorded 1.9.52 installer build.
- All sixteen patches applied successfully. Only patch text was replayed; historical shell commands were not executed.
- Per-patch timestamps, call IDs, file lists and hashes are in `v1.9.52-provenance.json`.
- Recovered application version is 1.9.52. Its installer source matches the pre-1.9.53 base; no 1.9.53–1.9.56 installer changes were copied.
- Regression validation: 98 Python tests and 11 frontend test scripts passed using temporary test data.

## Limits and safety

This is a reconstructed source candidate, not an independently preserved release snapshot or a demonstrated byte-identical rebuild of the original executable. No new installation package has been generated from it yet.

The historical installer uses image-wide taskkill for nssm.exe and CiscoNetworkManager.exe. Do not execute it on a host with other services/instances; this is a known safety defect, not a recommended installation method.

Original SQLite data, production services, original 1.9.52 installer and the current root working tree were not replaced. Recovery added only the isolated candidate, provenance and recovery tooling.

Requested device adapters, server/IP/rack corrections and certificate import must be added in a separate enhancement copy, preserving this recovered baseline. No automatic PostgreSQL migration is to be introduced.
