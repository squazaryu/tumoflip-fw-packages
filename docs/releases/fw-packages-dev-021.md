# FW Packages Dev 021 — Device Library and File History

Install **TumoCompanion 1.11.24**, then the complete **Tumoflip Dev 009-009**
firmware updater, then this package. Run **Verify on device** after installation.

Adds only:

- `apps/Tools/device_library.fap`: named device cards, notes, tags and links to
  existing files; no copying or moving original captures.
- `apps_data/device_library/plugins/file_history.fal`: opt-in checked checkpoints
  for supported native SUB/IR/NFC saves, with restoration into a separate copy.

All 117 predecessor payloads remain byte-identical; two additions make 119 files.
Earlier missed package updates remain included. Cleanup rules, user cards, captures,
Bluetooth names and pairing data are not replaced or removed.

## Bluetooth Remote

The persistent `Cannot select peer / Retry from Devices` controller repair is in
**firmware Dev 009-009**. This package retains Bluetooth Remote 1.3 byte-for-byte.
Reinstalling only the package or updating only the iOS app cannot fix older
firmware. Do not reset pairings. On the new firmware, test selecting a PC with
the phone nearby, unavailable-host waiting, explicit Add/cancel, reconnect and
return to Companion. Real-device acceptance is still pending.

## Compatibility and verification

Public F7 API remains 88.13. Device Library requires Tumoflip F7 API 88.5 or newer
in API 88; automatic history additionally requires the new firmware capability.
Historical catalog baseline fields stay unchanged because this is an independent
overlay, not a new firmware baseline. Tumo Acceptance 0.3 and TumoSpectrum 3.1 are
delivered by the firmware resources, not this catalog overlay.

Built on Ubuntu 24.04 with toolchain 39, bounded `-j2`, exact source
`6d2484a45a5b07ba9cb13613009e272858c06e17` and publisher
`586250c1cf841b68423a609d9ffd8e498fc923e5`.
Native composition/import checks, predecessor-byte comparisons and uploaded-byte
verification passed. No monitoring schedule, protected audit acceptance or stable
catalog baseline was changed. Hardware acceptance and power-loss testing remain
separate from publication.
