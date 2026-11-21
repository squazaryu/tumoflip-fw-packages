# FW Packages Dev034 — exact Dev 011-003 snapshot

This plan pins the complete native package snapshot from firmware
`t-dev-011-003`, commit `6ce06b32fd834111b0bcdd1d951c40b700923314`, API 88.23, F7.
The pinned manifest ID/JSON/ZIP digests come from the validated exact build
and must match the separately published firmware assets.

The immutable Dev033 catalog is the predecessor. All 143 paths and 34 cleanup
entries are retained. The only changed payload is RAW Edit 1.9 at the existing
ARF Tools path. It adds direct read-only opening of a selected RAW capture and
keeps the explicit create-new save. No existing file, favorite, data directory
or protected path is moved or deleted.

The [Dev034 release](https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-034)
and its exact firmware inputs were publicly downloaded and byte-verified.
Catalog activation advances only the Dev index. Do not
change Stable007, Dev033, old history, protected source decisions or Community
audit #214. TumoCompanion already supports this exact Dev snapshot; no IPA or
ESP32 update is required. Device acceptance remains for Dev testing.
