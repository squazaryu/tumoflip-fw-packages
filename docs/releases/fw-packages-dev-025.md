# FW Packages Dev 025

Published: https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-025

- Source: Tumoflip Dev 009-025, `69eb0bd34a69630eec76cabeb638da31c09fa0df`.
- Publisher: `0145d856ce2e8844c60a0b821bba7763ea0d6e49` (PR #162).
- CI build: https://github.com/squazaryu/tumoflip-fw-packages/actions/runs/38722692138
- Quac **0.12.0** and Weather Editor **2.5** fail safely on missing radio presets.
- Only those two FAPs changed from Dev 024; every other catalog member is
  byte-identical. User captures, configuration and keystores are not modified.
- API provenance **88.20**, target F7. These two FAPs add no new API imports.
- Independent manifest release ID:
  `593353f0d5ec2ac6fa9ee07700598835eaf721b1ab0230fd05f5f4f1813a16ca`.

Contract plus 315 tests passed. The pinned Ubuntu 24.04 / toolchain 39 build
passed source-owned tests and independent bounded-delta verification. The local
publisher reverified the downloaded artifact, then compared remote assets before
and after publication. Firmware updater/DFU and Stable were not modified by this
package publication. No iOS release was made. Device acceptance is pending.

In Companion, refresh FW Packages and select Dev 025 after firmware Dev 009-025.
Check Quac actions and Weather start/stop/Back on owned equipment. Keep backups
of important captures; testing must not delete or overwrite them.
