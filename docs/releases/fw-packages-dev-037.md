# FW Packages Dev037 — full Dev 011-005 snapshot

Complete ordinary package snapshot from firmware `t-dev-011-005`,
commit `85f6dbace5cb40e0420e290ff2ef4dd4136da2ea`, F7 / API88.23.
The exact published firmware manifest/ZIP digests are pinned in the native
release plan before composition or publication. All six public firmware assets
were downloaded without authentication and byte-verified against the release.

This snapshot accompanies the optional Panel desktop and activity/idle/recovery
work. It includes Field Tools 1.2 pending-object navigation; results remain
manual. Standard Desktop Settings arrives with the firmware updater resources.
Existing application paths and upstream author/license attribution stay intact.

Dev036 is the immutable predecessor. The complete 143-path inventory and all
34 cleanup entries are retained. This release adds no data migration, automatic
cleanup, new delete rule, app move or SD reset. The verified archives show 129
rebuilt FAP/FAL payloads changed and 14 payloads unchanged. This is a full
rebuild, not 129 new features; only the reviewed source changes are included.

The supported `firmwareSnapshot` scope manages the full package surface only
on exact clean firmware 011-005 with the matching source commit, F7 and API88.23.
Install the firmware first, then Dev037. TumoCompanion still uses device
checksums to avoid copying an already matching file. This full snapshot does
not remove the independent `delta` workflow for future FAP-only updates.

Before activation: verify exact-source Build/CodeQL, updater/SDK/DFU safety,
every public asset, the native publisher's independent recomposition, archive
inventory/cleanup parity and the released TumoCompanion 1.12.0 model. Publish
new immutable assets and activate the catalog in a separate PR. Stable007,
historical catalogs and protected-audit findings are not changed or cleared.
Physical-device UI, BLE reconnect and Field Tools acceptance remain Dev checks.

Published at [fw-packages-dev-037](https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-037),
release ID 459447446, publisher `0c80a25d6a0e84d876adb557d6c67345115bf4e9`.
All four public assets were downloaded without authorization and byte-compared;
the native verifier independently reproduced the full snapshot. The released
Companion 1.12.0 model accepted exact clean 011-005, rejected incompatible
identities and validated Field Tools old/new checksum states. The separate
activation updates only Dev and retains every historical catalog entry.
