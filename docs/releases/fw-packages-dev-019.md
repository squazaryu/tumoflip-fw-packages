# FW Packages Dev 019 — Bluetooth Remote 1.3

Install **Tumoflip Dev 009-005 / API 88.12 first**, then this package and run
**Verify on device**. TumoCompanion 1.11.23 is sufficient; no iOS update is required
for this release. Keep Bluetooth Remote protected from Community App replacement.

Exact firmware source: `cbcd5c07294ef3efe81bf638a05379650beb1b41`.
Only `apps/Bluetooth/hid_ble.fap` changes, to Bluetooth Remote 1.3:

- Unnamed bonds show readable `Device N` labels instead of raw addresses.
- On first selection enter a local name before connecting. Saved labels follow the
  bonded identity; existing labels, pairing keys and configuration are preserved.
- Back/invalid names/SD errors do not connect a new host. A selected host never falls
  back to the phone. Names are local, not automatically recovered OS hostnames.
- Regular and Stealth Mouse Jigglers stop/drain timers on Stop, Back and destruction,
  reset their running state, and wait for a BLE connection.

The cold-start `Cannot select peer / Retry from Devices` fix is in **firmware**.
Installing this FAP on 009-004 without updating firmware cannot fix its GAP readiness
check. USB Remote, RFID, iButton and Sub-GHz core fixes arrive with firmware 009-005,
not by replacing their files through this independent overlay.

All other Dev 018 payloads, dictionary data, prior package updates and cleanup rules
remain byte-for-byte unchanged. This is a cumulative catalog, not a delta download
requiring installation of missed revisions. No key, config, capture or firmware
flash file is supplied as replacement data.

The catalog keeps historical baseline API/identity metadata for independent overlay
compatibility. It is not a claim that Remote 1.3's complete flow works on that old
baseline: use Dev 009-005. This payload is built/import-checked against exact API 88.12.

Publication gates: Ubuntu 24.04/toolchain 39, `-j2`, exact source/import verification,
immutable predecessor and bounded-delta checks, plus uploaded-byte comparison. The
automated publisher remains disabled; no protected acceptance or monitoring policy
is changed. Physical acceptance remains pending.

After installation: cold-launch Bluetooth Remote, name/select the PC with the phone
nearby, test unavailable-host Waiting, Add device, cancel, rename persistence,
disconnect/reconnect, Stop/Back in both jigglers and return to Companion. Do not reset
pairings; use only a disposable test pairing for individual forgetting.
