# FW Packages lifecycle

FW Packages is an overlay catalog, not a firmware release. The catalog and the
firmware are two independent streams:

* a firmware release provides the baseline FAPs and the firmware API/target;
* a FW Packages release provides only files owned by the package catalog;
* `catalog-index.json` records every immutable package revision and its
  compatibility evidence.

The firmware version is deliberately absent from catalog identity. A catalog
revision is identified by `(channel, revision, release_id)`. Firmware metadata
may be retained as provenance, but it never makes a package revision belong to
one firmware release.

## Selection and rollback

The client downloads the signed/hashed `catalog-index.json` first. In Auto mode
it chooses the highest `active` revision whose declared target and API major
match the connected device. The user can open the stable history and select any
compatible `active` or migrated `legacy` revision. The selected manifest is a
complete overlay snapshot, so installing an older revision is the same atomic
transaction as installing a newer one.

Published releases and tags are never overwritten or deleted. If a package is
found to be unsafe, the next control-plane commit marks that revision
`withdrawn` in the index and publishes a corrected revision. A withdrawn
revision remains visible in audit history but is never offered for installation.

When an overlay removes an application, the generated manifest carries a
reversible cleanup entry. The installer moves the old target into its rollback
area only after the replacement snapshot has been staged and verified; a failed
transaction restores both versions.

## Additive data overlays

Line-oriented dictionaries are published as `data` releases, not as firmware
or FAP rebuilds. The publisher accepts only the checked-in `rfidfuzzer/` and
`ibtnfuzzer/` roots, validates the encoding, record width, hexadecimal values,
and duplicates, and records the exact bytes in `synced_data`.

Each data entry has a namespaced `tumoflip_*_vN.txt` filename and
`preserve_existing: true`. TumoCompanion stages and verifies the download, but
never backs up, removes, or replaces a different file already at that target.
An identical existing file is treated as an idempotent success; a different
existing file aborts before activation and leaves it untouched. This makes the
dictionary package additive and safe for user-maintained files on the SD card.

## Automatic Community Pack reconciliation

The scheduled reconciliation workflow resolves one exact Community Pack release,
its archive digests and commit, then runs both controls against that same source:

1. protected source parity checks that imported protected applications still
   correspond to the reviewed upstream source;
2. the protected-app audit ledger checks package bytes, paths, aliases and target
   hashes by app identity rather than by the display path.

The workflow writes a deterministic reconciliation report and keeps one
canonical review issue. If the exact source, byte, route and alias comparison is
verified, the issue is closed and no package release is produced. If anything
changes, the issue is opened or updated with the exact Community Pack commit,
parity result and audit identity. A package-generation PR is then created from
that reviewed decision; the scheduled audit itself never publishes bytes or
mutates a Flipper. Human review is required only for a protected source change
or a device-acceptance decision; routine catalog discovery no longer requires a
manual Verify action in TumoCompanion.

## Release procedure

1. Run `python3 tools/catalog_index.py validate` and the complete test suite.
2. Generate the candidate overlay snapshot from the exact Community Pack
   release and include its immutable audit evidence.
3. Allocate the next channel revision. Never reuse a tag or release ID.
4. Publish the manifest, ZIP and checksum sidecar as one immutable GitHub
   release.
5. Update `contracts/current-releases.json` and `catalog-index.json` in the same
   control PR. The index is what clients use for history and rollback.

Older schema-v2 releases remain readable during migration. They are represented
as `legacy` entries in the index until the client has received the independent
catalog implementation; no old release is silently deleted or rewritten.
