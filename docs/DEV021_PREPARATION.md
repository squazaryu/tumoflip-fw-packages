# FW Packages Dev 021 — preparation only

The candidate adds exactly two source-built files over immutable Dev 020:

- `apps/Tools/device_library.fap`
- `apps_data/device_library/plugins/file_history.fal`

All existing archive member payloads, cleanup entries and the cumulative managed
overlay inventory must remain unchanged. This is not a firmware snapshot or a
baseline promotion. Tumo Acceptance 0.3 and TumoSpectrum 3.1 belong to the new
firmware resources; neither path is managed by the independent Dev 020 catalog.

The additional Bluetooth Remote controller repair in PR #511 is firmware-only.
Dev 019 and Dev 020 carry identical Remote 1.3 bytes, which stay unchanged here.
Installing this package alone cannot fix `Cannot select peer` on older firmware.
Controller filtering/privacy and real-host acceptance require the new firmware.

The plan pins the exact source from Tumoflip PR #511. A later source change needs
an explicit plan update and a new verified build. This plan authorizes build and
verification, not publication or catalog activation. `current-releases.json`,
`catalog-lineage.json` and `catalog-index.json` are deliberately unchanged.

Device Library and its complete `apps_data/device_library/` family are protected
as one owner. There is no imported Community implementation to mark accepted.
No protected audit acceptance pins or upstream monitoring schedules are changed.

TumoCompanion 1.11.23 supports the unchanged catalog schema, but does not yet carry
the new default protection key. A small companion update is required before broad
delivery to protect the FAP and user cards/history against Community replacement.
Its managed-file compatibility gate should reject Device Library before SD writes
on Tumoflip API older than 88.5: the FAP imports `view_dispatcher_show_loading`,
first exported in 88.5. Automatic history separately needs the new live firmware
capability; old firmware must not report automatic protection as active.

Recommended order: updated companion, complete Dev 009-009 firmware/resources,
then Dev 021. Physical acceptance remains pending. The native workflow must run
with dry_run=true; publishing is a separate authorized step.
