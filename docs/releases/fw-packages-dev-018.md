# FW Packages Dev 018 — Bluetooth Remote

Install **TumoCompanion 1.11.23**, then **Tumoflip Dev 009-004 / API 88.11**,
then this package. Keep `hid_ble` protected in Community Apps / Protected apps.

Exact source: `4a61c5f6cb05c225996410b17690e2bb26de58d4`.
Only one new managed payload is added: `apps/Bluetooth/hid_ble.fap`
(Bluetooth Remote 1.2). It is the ordinary **Bluetooth → Bluetooth Remote**,
not BadUSB, USB Remote or the independent Kodi remote.

- Choose a bonded host before advertising; selected sessions permit only it.
- Missing host remains Waiting; selection failure never falls back to the phone.
- Remember/highlight the last chosen host, local labels, individual unpairing,
  and explicit host-side Add device mode. Disconnect other paired hosts while
  using open pairing. New pairing is not a nearby-device scanner.
- Keep existing HID keys/config. Store labels/selection separately and check
  writes, sync, close and readback. Corrupt/oversized pairing files fail closed.

All Dev 017 payloads, dictionary data, prior independent updates and cleanup
rules are preserved byte-for-byte. No firmware flash files are included and
no pairing/config/capture file is shipped as replacement data. USB Remote
remains bundled with firmware, and Toyota C is delivered in its built-in decoder.

The catalog retains its historical baseline metadata; this is not a claim that
the new Remote works on old API 88.0 firmware. Companion 1.11.23 checks the fresh
device identity and blocks this FAP before SD writes without Tumoflip F7 API 88.11.

Publication requires Ubuntu 24.04/toolchain 39 build (`-j2`), source API import
checks, immutable predecessor/delta verification and uploaded-byte comparison.
The automated publisher remains disabled; no monitoring schedule or protected
acceptance decisions are changed.

After installation run Verify on device, then test phone+PC target selection,
absent-host Waiting, reconnect, labels, pairing cancellation, individual removal,
and return to Companion. Physical-device acceptance is still pending.
