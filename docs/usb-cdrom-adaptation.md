# USB CD-ROM ownership preparation

The next authorized native content revision may add exactly
`usb_cdrom` → `apps/USB/usb_cdrom.fap` in Base, built from the reviewed exact
Tumoflip source. This policy change is not a release plan, publication, catalog
activation or protected-audit acceptance. Current Dev030 and Stable006 stay intact.

Retain every predecessor payload byte-for-byte unless a separate update is
explicitly selected. Do not use a full firmware snapshot as the package baseline.
No ISO images, user settings, captures or cleanup/deletion entries are added.

USB CD-ROM is managed rather than overwritten by the identically named Community
FAP. Protect its user image directory and the native archive/favorites-writer
plugin family. The latter is a firmware resource, not an independent FW Package
overlay. Do not replace the missing resource by weakening the firmware gate.
USB Floppy, CFS and AI Composer remain separate Community apps.

Before activating content, verify immutable predecessor/source/publisher refs,
F7 metadata/imports, embedded GPL notice, the bounded catalog delta and downloaded
asset digests. Advance no old protected-source/hardware acceptance pins merely
because the ownership or build tests pass.
