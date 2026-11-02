# FW Packages Dev031 — USB CD-ROM

Preparation record: **not published or catalog-active yet**. Dev030 and
Stable006 remain current until verified publication and a separate activation.

The exact overlay plan selects only `usb_cdrom` → `apps/USB/usb_cdrom.fap`
in Base, built from merged firmware source
`dba75d725d5cca882758150d9b0dc0041f8e7d0b` (Dev009-034 / PR575). Its validated
firmware manifest ID is
`015efe04b54a01093a8338a04323d2421eeb5283fb91fea66bb1bf61531141cd`;
target F7 / API88.23. No provisional source ref or public API bump is used.

## Bounded content

- Add protected USB CD-ROM0.2: explicitly chosen ISO, read-only SCSI/BOT handling, USB ownership/teardown and embedded GPL notice. Menu: Apps → USB → USB CD-ROM.
- Inherit every Dev030 member byte-identically. No other package rebuild, removal, data/dictionary overlay, ISO image, settings/history/capture replacement or cleanup-list change is authorized.
- ARF, Wardriving and the archive favorites writer are native Dev009-034 firmware resources. They are not converted to independent overlays; old reference entries outside the cumulative modified-target allowlist are not installed by a delta catalog.
- Keep old rollback entries, Stable006 and protected acceptance/implementation pins unchanged. The plan is not an audit acceptance verdict.

## Build / publish / activate

Run the existing read-only Ubuntu24.04/toolchain39 native job at the exact
reviewed publisher SHA with `dry_run=true`. Reverify its artifact against the
immutable Dev030 predecessor and the checked-in one-path plan at the manual
publication boundary. Firmware publication/checks must complete first; automatic
publishing remains disabled and workflow permissions are not expanded.

After new immutable assets are published and downloaded/byte-compared, activate
Dev031 through a separate current-release/lineage/index change and verify public
unauthenticated F7/API88 selection. Publication alone is not catalog activation,
device installation or hardware acceptance.

## Installation

Install the prepared TumoCompanion1.11.33 protection release when available, then
Dev009-034 including native resources, then refresh FW Packages and install
Dev031 **Base**. With older1.11.32, manually protect `usb_cdrom` and
`archive_favorites` before using Community Apps. Unchanged Module One / Protocol
Packs need no reinstall solely for this revision.

Back up important SD files first. Use a spare host/ISO for initial USB testing;
check host eject, cable removal and exit. No board/device is flashed by this
package publication. No idle-OOM/BLE repair or physical acceptance is claimed.
