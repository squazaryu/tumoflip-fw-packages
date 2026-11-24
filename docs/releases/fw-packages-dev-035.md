# FW Packages Dev035 — full Dev 011-004 snapshot

Pins the complete ordinary package snapshot from firmware `t-dev-011-004`,
commit `83f6f3a24bd25f2a2b3726d5920e4c2d30e12d2c`, F7 / API 88.23.
The source contains the BLE bond-storage fix (#598) and the native regression
carried by firmware release PR #600. It is not a BLE-QA profile.

The immutable Dev034 catalog is the predecessor. All 143 package paths and 34
cleanup entries are retained. Only the existing Field Tools FAP changes;
its linked-file check reports Present / Empty / Missing using metadata.
The pre-existing ARF modules remain byte-identical to Dev034. No user-data
migration, path move or additional cleanup is introduced.

Firmware updater/DFU, SD data, Stable007 and historical catalogs are not
modified by this package release. Install firmware Dev 011-004 first, then
select Dev035. API is unchanged; a new TumoCompanion or ESP32 release is not
required by this package identity.

The [firmware](https://github.com/squazaryu/tumoflip/releases/tag/t-dev-011-004)
and [Dev035 package](https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-035)
were published and every public asset was anonymously downloaded and
byte-verified. Catalog activation advances only Dev; Stable and all history
are preserved. Hardware BLE reconnect remains a Dev acceptance step.
