# FW Packages Dev030 — Field Apps

Exact source: `513a55a3615f5d80e2ecb029a92f8e3c08d26e1a`, the merged Dev009-033 release source.
F7 / API88.23. Target firmware manifest ID:
`de00b93f2267bfd1ef6e731c98ae0322e0dc4b318d9fdf0c1ac74ea4c2651e0b`.

This plan authorizes the existing read-only native build and, after successful
verification, the user-requested manual publication transaction. It does not
activate the catalog or mark any pending protected audit accepted. Automatic
publication remains disabled; no workflow permissions or release policies change.

## Exact bounded content

- Five new Base FAPs: Servo Tester+1.0, Power Journal1.0, Sensor Watch1.0,
  Varta Inspector1.0, Tumo UHF Inventory1.0.
- Update Base Device Library1.3 and Module One TumoSpectrum3.6 for deferred
  Sensor Watch routes and object/global sensor histories.
- Inherit the other130 Dev029 payloads byte-identically. Expected complete
  cumulative catalog:137 payloads, two changed existing members, five added,
  no removal or cleanup-list change. Unrelated source-snapshot rebuilds and
  Tumo RX Link are not selected.
- No dictionary/data overlay, no user-history/report replacement, no iOS or
  ESP32 image, and no emulator payload.

Firmware and packages are separate releases. Install Dev009-033 first, then
refresh FW Packages and select Dev030 **Base + Module One** using existing
TumoCompanion1.11.32. Firmware flashing alone does not install the new FAPs.

See firmware `docs/releases/t-dev-009-033.md` for menu routes and the manual
hardware checks after installation. Measure servo PWM before attaching an
actuator. UHF needs a separate powered UCM601 reader; Module One/C5 alone does
not provide UHF RFID. Power/sensor sampling is app-only, not background work.
Varta observations/format matches do not prove authenticity or access rights.

Old catalogs, Stable006, recordings, object cards and SD backups are preserved.
Publication verification, separate catalog activation and device acceptance
must be reported independently. Physical and independent review are not claimed.
