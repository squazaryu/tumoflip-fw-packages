# FW Packages Dev 022 — Capture Inspector and TumoSpectrum

Install with Tumoflip Dev 009-012 (F7, API 88.14), then select **FW Packages Dev
022** in TumoCompanion and run **Verify on device** after installation.

This revision updates exactly two managed files over immutable Dev 021:

- Capture Inspector 1.1: compare two or three saved `.sub`/`.psf` captures,
  report stable/changed/missing fields, and export a read-only report.
- TumoSpectrum 3.2: show preset/protocol configuration differences in capture
  comparison and reports; mismatched settings are not labeled “Likely same”.

All non-selected package payloads, cleanup entries, dictionaries, and user data
remain unchanged. The release is an overlay revision, not a firmware snapshot or
a baseline promotion.

Built and verified from firmware source
`9531d28b88970df05423c538cefd7966bd08e8af` with API 88.14. Release provenance
binds it to the immutable Tumoflip Dev 009-012 release and Dev 021 predecessor.
Physical installation and device acceptance remain pending until tested on a
Flipper Zero.
