# FW Packages Stable007 — Stable010 matching snapshot

Preparation, not activation. Exact firmware source:
b3e9aacd75a342d66fd0f9dea0467b1217726a59,
[v1.0.10](https://github.com/squazaryu/tumoflip/releases/tag/v1.0.10), F7/API88.23.
All six firmware assets were anonymously downloaded, hashed and their native,
SDK/API/embedded-source/updater/DfuSe4KiB boundaries revalidated before pinning.

Compose142 native payloads from that exact published firmware manifest/ZIP.
Preserve exactly the two hash-pinned, preserve_existing predecessor dictionaries
utergrooll_em4100 andutergrooll_ds1990; do not remove previously shipped content
merely because it is absent from the firmware-native archive. Native FAP/FAL
bytes must remain byte-identical to the canonical firmware snapshot. Total144.

This is not a Community Pack binary import or protected acceptance update.
Stable006 and Dev031 rollback contracts and all protected pins remain unchanged.
The current Stable pointer remains006 until the new immutable assets have been
downloaded/verified and a separate activation PR advances lineage/index/history.

Use the existing firmwareSnapshot composition/verification helpers on the
verified public firmware and immutable predecessor, at the exact merged control
commit. No additional FAP/firmware build or on-device write is performed by this
pure composition. Publish manifest,ZIP,SHA256SUMS andcatalog-provenance.json;
reverify every download and only then activate.

Install Stable010 including native resources first, then Stable007 packages.
TumoCompanion1.11.35 supports exact-version firmwareSnapshot catalogs. No reset,
SD cleanup, iOS release or automatic board flash. Modified user dictionaries are
not overwritten (preserve_existing remains true). Physical acceptance and fresh
independent review are not claimed.
