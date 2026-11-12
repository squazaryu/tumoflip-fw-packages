# FW Packages Stable007 — Stable010 matching snapshot

Published and download-verified on 2026-11-12. Exact firmware source:
b3e9aacd75a342d66fd0f9dea0467b1217726a59,
[v1.0.10](https://github.com/squazaryu/tumoflip/releases/tag/v1.0.10), F7 / API 88.23.
All six firmware assets were anonymously downloaded, hashed and their native,
SDK/API/embedded-source/updater/DfuSe 4 KiB boundaries revalidated before pinning.

Compose 142 native payloads from that exact published firmware manifest/ZIP.
Preserve exactly the two hash-pinned, preserve_existing predecessor dictionaries
utergrooll_em4100 and utergrooll_ds1990; do not remove previously shipped content
merely because it is absent from the firmware-native archive. Native FAP/FAL
bytes remain byte-identical to the canonical firmware snapshot. Total: 144.

This is not a Community Pack binary import or protected acceptance update.
Stable006 and Dev031 rollback history and all protected pins remain unchanged.
The separate activation advances the Stable pointer to 007 and the next revision
to 008, while preserving every previous catalog-index entry and Dev031 unchanged.
Published release 442368008 targets control 458ba3e05730a72151117fa21e4464a4d63d6b57.
Manifest release ID: 7885dfac77074a33fbf547f74080fd160b8c8d48d23557a121e0a2fcabc3c487.
All four assets were downloaded from the draft and again anonymously after
publication, SHA256-checked and compared byte-for-byte with the composed result.
The public archive also passed independent native-release reconstruction checks.
The audit-target registry records these exact published identities for routing;
it does not advance any protected source-acceptance decision.

Use the existing firmwareSnapshot composition/verification helpers on the
verified public firmware and immutable predecessor, at the exact merged control
commit. No additional FAP/firmware build or on-device write is performed by this
pure composition. Published manifest, ZIP, SHA256SUMS and catalog-provenance.json;
reverify every download and only then activate.

Install Stable010 including native resources first, then Stable007 packages.
TumoCompanion 1.11.35 supports exact-version firmwareSnapshot catalogs. No reset,
SD cleanup, iOS release or automatic board flash. Modified user dictionaries are
not overwritten (preserve_existing remains true). Physical acceptance and fresh
independent review are not claimed.
