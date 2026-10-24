# FW Packages Dev 026

Published: https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-026

- Source: Tumoflip Dev 009-029, `fa3b71d8518a182909ea7c5834613752687bbb12`.
- Publisher: `e6a6a5f8a6fa7651804f622956d4279373a01218` (PR #174).
- Native CI: https://github.com/squazaryu/tumoflip-fw-packages/actions/runs/40089235733
- **Quac 0.12.1**: held/released frame completion, RAW EOF handling, cancellable
  waits and preservation of Repeat/custom-button state.
- Checked signal saves verify a new backup, byte counts, truncate/flush/close
  and read-back; failures attempt rollback and retain the verified backup.
  This is **not atomic power-loss protection**. Keep important captures backed up.
- Only `apps/Tools/quac.fap` changed from Dev 025; all other119 catalog members
  remain byte-identical. No capture/configuration/keystore cleanup is added.
- The cumulative delta retains Weather Editor from Dev 025, so installing the
  newest catalog also includes that earlier update when a revision was skipped.
- API provenance **88.22**, target F7. This is an overlay, not a full baseline
  rebuild. Independent manifest release ID:
  `3b184f778f8e09756c36bc62139dcb26aa0d9aa480204c4041ad7fe94420e4fd`.
- Build tooling uses hash-pinned Pillow13.0.0, replacing the affected12.3.0 pin.
  Pillow is not shipped as a Flipper runtime dependency.

Control contract and334 tests passed. The pinned Ubuntu24.04/toolchain39 native
build passed source-owned tests and a separate bounded-delta/provenance verifier.
The explicit publisher checked remote bytes before and after publication, and all
four public assets were downloaded once more and byte-compared with the CI
artifact. Automatic publishing remains disabled; this release used the existing
explicit, resumable publisher. Audit evidence is pinned to published assets,
not marked as hardware acceptance or a passed protected-app review.

Install **firmware Dev 009-029 first**, then refresh Companion's FW Packages and
select **Dev 026**. Check Quac hold/release, RAW completion, Back cancellation and
save/reopen using disposable copies of your own recordings. Hardware acceptance
and independent agent review remain unperformed. No iOS/ESP32 release, device
flashing, Stable change or audit-disposition update is part of this package.
