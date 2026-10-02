# FW Packages Dev 023 — Specter 3.3 preparation

This is an independent, Specter-only overlay over the immutable
`fw-packages-dev-022` catalog. Its source is the exact merged Tumoflip Dev
commit `42aad5d7e16cdb430fd7bbed1c37cf5164b3a334`. It targets the already
published `t-dev-009-016` firmware (F7, API 88.14); no firmware flash asset or
public API changes in this package release.

The only authorized replacement is `apps/NFC/specter.fap`. The native release
verifier must prove that all other ZIP payloads, cleanup entries, data files,
and the cumulative package inventory remain unchanged from Dev 022. Specter
remains protected against Community Pack replacement. Existing Specter settings
are migrated in place from v2 to v3 by the FAP, without erasing user logs.

The owner approved the native UI preview. CI and local APPCHK cover the source
and FAP, but physical NFC detection, old settings on SD, splash navigation, and
BLE coexistence remain unverified. This preparation plan does not advance
`current-releases.json`, `catalog-lineage.json`, or `catalog-index.json` and
does not mark those hardware checks complete. Publication and catalog activation
are separate, verified steps.
