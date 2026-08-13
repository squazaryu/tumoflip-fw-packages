# Zero-break migration

## Phase 0 - freeze and inventory

1. Keep every existing `fw-packages-*` release and tag in
   `squazaryu/tumoflip` immutable.
2. Record the exact `stable-001` and `dev-008` manifest, archive, checksum,
   release ID, tag commit, and asset digests in `contracts/legacy-sources.json`.
3. Keep firmware releases and their base `tumoflip-packages.*` snapshot assets
   in the firmware repository. They are firmware evidence, not the independent
   package channel.

## Phase 1 - seed this repository

Run the seed tool against the legacy repository, verify the resulting index,
then create byte-for-byte mirror releases for only the current channel heads:

```bash
python3 tools/seed_legacy.py --output .seed
python3 tools/verify_catalog.py contract --root .
python3 tools/verify_catalog.py seed --root .seed
```

The mirror release carries the original three package assets plus
`migration-provenance.json`. The new repository tag points to the seed commit;
the provenance file binds that publisher commit to the original repository,
tag commit, source commit, and exact asset hashes. Mirroring must not regenerate
or re-zip either package.

## Phase 2 - dual-read client

TumoCompanion reads `squazaryu/tumoflip-fw-packages` first. It consults the
legacy repository only for transport failure, HTTP 404, or an absent compatible
channel. It must not fall back when the primary returns malformed JSON, a digest
mismatch, a rollback/revision collision, or incompatible evidence.

Catalog identity is `(catalog_channel, catalog_revision, release_id)`. If the
same identity exists in both repositories, the primary record wins and its
provenance is not concatenated with the fallback record.

## Phase 3 - native publication

Publish the first newly built catalogs as `fw-packages-dev-009` and
`fw-packages-stable-002`. The workflow checks out `squazaryu/tumoflip` by a full
40-character commit SHA, verifies `git rev-parse HEAD`, builds with `-j2`, and
records both source-repository and publisher-repository provenance.
Each candidate is a bounded delta over the exact mirrored channel head; it may
replace only the control-owned overlay paths. This prevents a package-only
release from presenting unrelated firmware-built applications as updates.
The exact subset and firmware-source commit must be checked in per release.
Neither Dev009 nor Stable002 currently has a plan: the first Dev009 experiment
changed only the `.gnu_debuglink` CRC in `esp_flasher`, with identical runtime
bytes, so authorizing it would create a false update. Both channels therefore
fail closed until an actual source/runtime change is separately approved.

Current subphase: the native builder and dormant publisher transaction are
implemented, but workflow build and publication are fail-closed while there is
no release plan. Do not grant write permission or connect
`tools/publish_native.py` until the candidate passes legacy-composition
equivalence review and physical-device package installation/verification.

Only after both channel heads install and verify on physical hardware may the
legacy package workflow be disabled. Do not remove old releases or client
fallback in this phase.

## Phase 4 - independent protected audit

Move audit orchestration, decisions, and ledger publication here. The audit
still checks out firmware implementation source at an exact commit. Publish an
immutable `audit-ledger-YYYYMMDD-NNN` release containing:

- `protected-app-audit-ledger.json`
- `protected-app-audit-ledger-SHA256SUMS`
- `audit-provenance.json`

`protected-app-audit-ledger/latest.json` may remain as a transitional raw
endpoint. TumoCompanion prefers the immutable release asset, then the new raw
endpoint, then the old raw endpoint only when the newer endpoints are
unavailable. Invalid primary audit data is terminal and must not fall back.

The migration seed is content-addressed in `audit/bootstrap/index.json`. Keep
all historical files byte-for-byte, including generator snapshots that are not
valid as a current client response. Only `latest.json` is required to satisfy
the strict schema-2 validator and the unique client identity
`(targetMD5, channel, releaseTag, manifestSHA256)`.

Publication is deliberately ordered: immutable release first, transitional
raw branch second, issue reconciliation last. A rerun may finish a raw-branch
or issue step after an already verified immutable release; it must never alter
that release. The old workflow must remain available until a released
TumoCompanion build has exercised the new immutable release and new raw URL.
After that acceptance, disable writes in the legacy workflow but preserve its
raw branch, issues, releases, and tags as historical evidence.

## Rollback

- Never delete or retag a published revision.
- If a valid but bad package escaped validation, publish the next revision with
  the corrected bytes and mark the superseded release withdrawn in the catalog
  index. Cached clients must honor withdrawals before offering installation.
- If the new repository is unavailable, clients may use only the exact
  allow-listed legacy heads in `legacy-sources.json`.
- Re-enabling the legacy publisher requires an explicit incident decision; it
  must continue at a new revision and must not overwrite an old tag.
