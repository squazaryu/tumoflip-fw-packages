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
- Compatibility is explicit: target `f7`, API range, firmware release ID, and
  retained compatible build hashes.
- A tag, revision, or release ID collision with different bytes is fatal.
- `dev-009` and `stable-002` are reserved as the first native releases here.

## Build boundary

The publisher receives only full commit SHAs. It performs an exact checkout of
the firmware repository and verifies the checkout before executing its package
builder. A read-only build job produces an artifact and verification report. A
separate environment-protected job with `contents: write` publishes only that
verified artifact.

## Protected audit releases

Audit releases use their own tag namespace and cannot be selected as FW
Packages. The client-visible provenance identity is exactly:

```text
(targetMD5, channel, releaseTag, manifestSHA256)
```

Generator-only evidence must be deterministic and must never publish two
records with the same client-visible identity.
