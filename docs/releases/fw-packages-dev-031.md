# FW Packages Dev031 — USB CD-ROM

Published: https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-031

Immutable prerelease **431194924**, published2026-11-02T07:57:26Z. Exact native
Ubuntu24.04/toolchain39 CI **41408555131** succeeded; artifact **13123994612**
was reverified at the manual publication boundary. All four public assets were
downloaded anonymously and byte-compared. Manifest ID:
`daab1695fbe787bef743bed50b4fc13ac3524863cda5ae4b19b72c3a5ba08968`.

The separate activation consumes the031 plan and advances only Dev to031/next032.
It must merge before the app selects031 automatically. Hold activation until
the prepared iOS protection release is publicly available. Stable006, old
rollback identities and protected acceptance/implementation pins are unchanged.

The exact overlay plan selects only `usb_cdrom` → `apps/USB/usb_cdrom.fap`
in Base, built from merged firmware source
`dba75d725d5cca882758150d9b0dc0041f8e7d0b` (Dev009-034 / PR575). Its validated
firmware manifest ID is
`015efe04b54a01093a8338a04323d2421eeb5283fb91fea66bb1bf61531141cd`;
target F7 / API88.23. No provisional source ref or public API bump is used.

## Bounded content

- Add protected USB CD-ROM0.2: explicitly chosen ISO, read-only SCSI/BOT handling, USB ownership/teardown and embedded GPL notice. Menu: Apps → USB → USB CD-ROM.
- Verified complete catalog:138 payloads,137 inherited byte-identically and one new USB CD-ROM FAP. No other package rebuild, removal, data/dictionary overlay, ISO image, settings/history/capture replacement or cleanup-list change.
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
