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
- A firmware-only release does not create a new package catalog. A package
  revision advances only when its manifest, archive, or accepted provenance
  changes; otherwise the existing catalog remains valid for compatible
  firmware revisions.
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
Its raw audit branch is imported byte-for-byte from commit
`a95ee7dc6d8add5e5f4b25e7abbb426634fd0dca`; the checked-in bootstrap contract
pins the tree, every history object, and the strict schema-2 client ledger.

## Repository boundary

This repository owns catalog lineage, release assets, audit publication,
schemas, and distribution workflows. The firmware repository continues to own
application source, `fbt`, firmware builds, API checks, and firmware releases.
Package workflows check out that repository by exact commit and never copy its
source here.

Stable002 is the first native stable release. Stable003 is the current stable
catalog and promotes the exact package snapshot published with firmware
`v1.0.6`; its ZIP payload is byte-identical to that firmware release and its
catalog identity is independent. Dev009 remains
fail-closed without an authorized source/overlay plan. Overlay builds start from
the exact current catalog and may replace only reviewed paths, so build-only
metadata drift cannot become a false application update. Stable firmware
promotions use the separate exact-snapshot mode and pin the firmware tag,
commit, release ID, manifest hash, and ZIP hash.

The protected-app workflow likewise executes only this repository's audited
scanner. Firmware and Community Pack checkouts are read-only evidence. Package
and historical firmware targets are numeric-release-ID contracts; each audit
also resolves exactly one latest official Tumoflip stable release and one dev
prerelease through a fixed, fail-closed selector. The selected numeric release
ID, peeled tag commit, updater digest, resource manifest and resources digest
are frozen into the immutable audit evidence before any device bytes are
accepted. Publication is blocked unless GitHub reports immutable releases
enabled.

## Read-only upstream watch

`upstream-unleashed-watcher.yml` polls the public Unleashed `dev` branch and
published releases on a separate schedule. Its reviewed boundary is the exact
commit and release in `contracts/upstream-watchers.json`; the workflow cannot
advance that file, modify Tumoflip, merge an upstream change, or publish a
firmware/package release. It only maintains one canonical human-review issue
with exact branch/release refs and merged-PR candidates. A reviewer records the
decision first, then advances the boundary only through a separate reviewed
control-plane pull request.

See [Migration](docs/MIGRATION.md), [Release contract](docs/RELEASES.md), and
[Security](SECURITY.md).
