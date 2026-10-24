# FW Packages Dev027 — Device Library1.1

Published: https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-027

- Source: Tumoflip Dev009-030, `6c07f477ea651541d9fcfa7f06c23d8a8ff43c85`.
- Publisher: `52ff27c2ab2722d04fe0af0040c374c052affb64` (PR176).
- Native CI: https://github.com/squazaryu/tumoflip-fw-packages/actions/runs/40134863418
- **Field wallet:** up to8 named file actions and16 pinned objects. NFC/RFID/
  iButton open their native saved-file menus without automatic emulation; SUB/IR
  retain their native file flow. Unknown formats/scripts stay reference-only.
- Checked rotating card/pin writes require sync, close and exact generation/SHA256
  readback. Missing files/drivers and unsaved changes fail closed. Removing a link
  or pin never deletes an object or its original capture file.
- Only `apps/Tools/device_library.fap` changed from026. All other119 members,
  including the history FAL/private ABI2, remain byte-identical. No cleanup of
  user recordings, configurations, card data or keystores is introduced.
- Cumulative catalog delta retains Weather Editor and Quac updates from earlier
  revisions. API provenance88.22, F7. This is not a full catalog rebase.
- Manifest ID: `f0837663a8ab128c2f9019eee2e7c015eb45c56ea088ed91f8d4b6a77716bc6f`.

Control contract and334 tests passed. Pinned Ubuntu24.04/toolchain39 build and
separate bounded-delta/provenance verification passed. The explicit publisher
downloaded and verified bytes before/after publication; all4 assets were then
downloaded and byte-compared once more. Automatic publication stays disabled.
Protected-audit inputs pin these real assets; dispositions/acceptance are unchanged.

Install **firmware009-030 including native resources first**, then refresh
Companion's FW Packages and install **Dev027 > Base**. No iOS/ESP32 update is
needed. Open Device Library under Tools, test old cards, rename/save actions,
pin/unpin and open your own capture files. Confirm there is no automatic emulation
and that removing shortcuts leaves original files intact.

Old cards are read without rewriting. Explicit Save writes TDC2 revisions;
Device Library1.0 cannot read new revisions after downgrade. Retain your own
card-data backup. Physical-device acceptance and independent agent review are
unperformed. No hardware flash, Stable change or protected-review approval is
part of this package release.
