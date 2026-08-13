# Tumoflip FW Packages

This repository is the distribution and audit control plane for Tumoflip FAP
packages. Firmware source and firmware images remain in
[`squazaryu/tumoflip`](https://github.com/squazaryu/tumoflip).

## Trust model

- Every package is built from a full, immutable commit SHA of the firmware
  repository. Branch names are never accepted as build inputs.
- Release assets are verified by manifest `release_id`, SHA-256, size, ZIP
  membership, and target-path policy before publication.
- `stable` and `dev` package revisions are independent from firmware versions.
  Compatibility remains fail-closed on Flipper target, API, firmware lineage,
  and manifest evidence.
- Published releases are immutable. Corrections use a higher revision; releases
  and protected tags are never rewritten.
- Protected-app audit results use their own immutable release stream and do not
  share client-visible provenance identities.

## Channels and tags

| Stream | Tag | First native release |
| --- | --- | --- |
| Stable packages | `fw-packages-stable-NNN` | `fw-packages-stable-002` |
| Dev packages | `fw-packages-dev-NNN` | `fw-packages-dev-009` |
| Audit ledger | `audit-ledger-YYYYMMDD-NNN` | next successful audit |

The exact legacy `stable-001` and `dev-008` assets are seeded as byte-for-byte
mirrors so clients can switch repositories without losing the current catalog.
The legacy firmware repository remains an immutable fallback during migration.

## Repository boundary

This repository owns catalog lineage, release assets, audit publication,
schemas, and distribution workflows. The firmware repository continues to own
application source, `fbt`, firmware builds, API checks, and firmware releases.
Package workflows check out that repository by exact commit and never copy its
source here.

See [Migration](docs/MIGRATION.md), [Release contract](docs/RELEASES.md), and
[Security](SECURITY.md).
