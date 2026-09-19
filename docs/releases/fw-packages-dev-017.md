# FW Packages Dev 017

Install TumoCompanion **1.11.22** and Tumoflip Dev **009-003** first.
Keep Specter protected in Community Apps / Protected apps.

Exact source: `fe7e1babfdb093cac53d6384b795acda5aa01e40` (API 88.10).
This bounded overlay release changes four applications only:

- `apps/Tools/quac.fap`: Quac 0.11.0 duration validation, accurate waits and cancellation ownership.
- `apps/NFC/specter.fap`: maintained 3.1.0-tumo passive NFC field diagnostics with checked log I/O.
- `apps_data/arf_subghz_full/packages/capture_inspector.fap`: read-only saved-record inspection/comparison and report export; open ARF → Capture Inspector.
- `apps_data/arf_subghz_full/packages/protopirate_to_subghz.fap`: the file-only Capture converter previously shipped with firmware 009-002; open ARF → Capture converter.

All other Dev 016 payloads, data dictionaries and cleanup rules remain byte-for-byte
unchanged. Earlier independent package updates remain cumulative. No firmware
flash file or user's capture/log/settings file is replaced by this catalog.

The catalog retains its historical identity/compatibility baseline. That metadata
does **not** prove compatibility with old API 88.0 firmware. The four applications
are built and import-checked against the exact API 88.10 source; use 009-003.

Publication requires the contracted Ubuntu 24.04/toolchain 39 build with `-j2`,
native bounded-delta verification, an immutable predecessor digest match and
downloaded-asset byte verification. The dormant automated publisher remains
disabled; this does not change schedules or LLM monitoring.

Hardware acceptance remains pending: verify the package files on device, open all
four apps, test Quac Back/cancel and duration limits, Specter field/calibration/log
errors, and Inspector/converter cancellation without modifying source recordings.
An unchanged protected-audit decision is not new acceptance evidence.
