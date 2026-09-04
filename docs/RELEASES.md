# Release contract

## Package assets

Every package release has these canonical assets:

- `tumoflip-packages.json`
- `tumoflip-packages.zip`
- `fw-packages-<channel>-NNN-SHA256SUMS`
- `catalog-provenance.json` for native releases, or
  `migration-provenance.json` for an exact legacy mirror

The tag-scoped checksum file covers the manifest and ZIP. The provenance sidecar covers
all canonical assets, the publisher repository and commit, and the exact
firmware-source repository and commit.

## Revision rules

- A revision is monotonically increasing within its channel.
- Package revision and firmware version are independent values.
- `package_release.catalog_install_scope` is explicit: `delta` exposes only the
  cumulative package-owned target allowlist, `baseline` exposes a complete
  firmware-owned package surface with zero managed overlays, while
  `firmwareSnapshot` exposes the complete firmware package snapshot and is
  installable only on that exact firmware identity. A baseline is compatible by
  channel, API major, and hardware target; it is not pinned to one firmware
  release.
- Compatibility is explicit: target `f7`, API range, firmware release ID, and
  retained compatible build hashes.
- A tag, revision, or release ID collision with different bytes is fatal.
- `stable-002` and `stable-003` are historical firmware snapshots. `stable-004`
  is the first independent stable baseline. `dev-008` is the migrated legacy
  development head; the next native revision is allocated only when the
  independent overlay snapshot actually changes.

The complete rollback inventory is `catalog-index.json`. It is an append-only
control-plane document, not a replacement for the immutable release assets. Auto
selection uses the highest compatible `active` revision; manual selection may
use any compatible `active` or migrated `legacy` revision. A bad release is
marked `withdrawn` in the index and corrected at a new revision. The index is
never allowed to point at a missing, withdrawn, or digest-mismatched current
release.

## Build boundary

The publisher receives only full commit SHAs. It performs an exact checkout of
the firmware repository and verifies the checkout before executing its package
builder. A read-only build job produces an artifact and verification report. A
separate environment-protected job with `contents: write` publishes only that
verified artifact.

During the migration gate the second half is intentionally disabled: the
environment job has read-only permissions and exits. The build job also fails
closed until a reviewed release plan exists. `tools/publish_native.py` is
covered by transaction tests but is not reachable from Actions.

## Native build ownership

The exact firmware checkout owns application source, `fbt`, and the
`PACKAGE_RELEASE_OVERLAY_FILES` / `package_extapp_exports` mapping. This
repository owns the independent-catalog delta composition and will accept only
paths that resolve unambiguously through that source-owned mapping. A checked-in
per-release plan pins the only authorized source commit and either a non-empty
subset of allowlisted overlays or an exact stable-firmware snapshot. There is no
implicit "rebuild all" default. Overlay releases remain blocked until they
contain an actual runtime change.
Before composition, the workflow downloads the current immutable package
catalog from this repository and verifies its release ID, checksum, ZIP, and
contract-pinned asset hashes. The source builder overlays only the separately
reviewed paths in `contracts/native-build-policy.json`; rebuilding the other
applications from a newer firmware checkout is forbidden because it would
create false mass updates.

An allowlist entry grants build capability, not release authorization. Quac is
reserved only as the `base` overlay `apps/Tools/quac.fap`; no
`apps_data/quac` path is package-owned. `fw-packages-dev-013` remains blocked
until a separate reviewed plan pins the exact merged firmware source commit and
selects only `quac`. The current release contract and rollback index advance
only after the candidate is built, independently verified, and published as an
immutable release.

`tools/native_release.py` then:

1. proves the full source SHA and a clean tracked checkout;
2. requires the channel's pinned firmware tag, commit, release ID, version, API,
   and target, independently from the predecessor catalog release ID;
3. preserves cleanup entries, firmware artifact evidence, and every non-overlay
   ZIP member payload byte from the immutable predecessor. A reviewed overlay
   may replace an existing package or add one new package path; additions must
   be assigned to an explicit package group and use the canonical `/ext/...`
   target derived from that source path;
4. adds `catalog_channel`, `catalog_revision`, and the immutable release tag;
5. recomputes the content-addressed manifest ID;
6. emits a two-asset checksum file and `catalog-provenance.json`;
7. pins the CI image, records the source toolchain version and exact built FAP
   hashes, and normalizes ZIP container order, timestamps, and permissions;
8. independently verifies manifest, archive members, hashes, sizes, paths, and
   the bounded delta against that predecessor.

Selecting an overlay whose newly built digest equals the predecessor is a
terminal no-op error. A FAP differing only in its `.gnu_debuglink` CRC is also a
runtime no-op and is rejected. FAP payload reproducibility is not assumed;
exact source-built bytes are recorded and independently checked.

Firmware DFU, update, SDK, updater, or radio assets are never downloaded,
rewritten, checksummed into, or uploaded by this path.

### Data-only package revisions

Small, publisher-owned SD datasets (for example, authorized RFID/iButton test
dictionaries) use `mode: data`. They are composed from checked-in text files,
not from an `fbt` build, and are limited to the allowlisted data roots. The
manifest marks every such entry with `kind: dictionary` and
`preserve_existing: true`. The client may add the file when the target is
absent, accepts an identical target as a no-op, and fails closed when a
different file is already present. It never overwrites user data or performs a
directory-wide cleanup. A later revision must use a new namespaced filename or
an explicit reviewed migration; it cannot silently replace a user's file.

For a stable firmware promotion, the publisher consumes only the package
manifest and package ZIP already published by the exact firmware release. It
verifies their pinned digests, firmware identity, release ID, and every archive
member, then adds independent catalog metadata without changing any ZIP payload
byte. The firmware updater/DFU itself is never copied into FW Packages.

The dormant publisher uses a resumable transaction: create a draft through the
REST API (retaining its returned release ID), upload only missing assets,
download and byte-verify all assets, publish by release ID, then download and
verify again. At the privilege boundary it also requires the exact
contract-pinned predecessor assets and repeats the bounded-delta verification.
Any unexpected, partial-public, or mismatching release is terminal and is never
clobbered.

## Protected audit releases

Audit releases use their own tag namespace and cannot be selected as FW
Packages. The client-visible provenance identity is exactly:

```text
(targetMD5, channel, releaseTag, manifestSHA256)
```

Generator-only evidence must be deterministic and must never publish two
records with the same client-visible identity.

Each audit release contains exactly the ledger, its tag-scoped checksum file,
and `audit-provenance.json`. Provenance binds the control commit, exact
Community Pack archives, canonical issue, exact firmware implementation,
numeric package/firmware release IDs, and all asset digests. Mirror tag commits
are publisher commits and therefore are not required to equal the firmware
source commit recorded inside the mirrored manifest; exact
`migration-provenance.json` supplies that binding.

Public audit releases are append-only. The publisher may resume its own draft
by numeric release ID, but an existing public mutable release, unexpected
asset, digest mismatch, tag drift, duplicate client identity, or malformed raw
ledger is terminal.
