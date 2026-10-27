# FW Packages Dev029 — TumoSpectrum Envelope Check

Published: https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-029

- Immutable prerelease425568156, published2026-10-27T14:29:19Z.
- Publisher `be56902e60af2d0abeca20d97cc6a5c4fd82516b` (PR182).
- Native Ubuntu24.04/toolchain39 CI40520226292 passed. All4 public assets were
  downloaded and byte-compared with the verified CI artifact, before/after
  publication and with a fresh post-publication download.
- Exact132-member snapshot: **one changed**, **131 byte-identical**, no removals.
- Manifest ID `66792c10fe0bc468c06bcb6ef81e4dec09743c301998791850192d32cac05c78`.
- Separate catalog activation consumes the029 plan and advances the next native
  revision to030. Prior revisions/rollback entries and Stable006 remain intact.

- Update only `signal_workbench` / **TumoSpectrum 3.5** in **Module One**.
  Keep every other Dev028 payload byte-identical; no path is removed.
- Exact package source: `d8b4500ccf97014b079d2161f7d98b39d33978aa`.
  This single-parent package snapshot has the exact tracked tree of reviewed
  Dev `a6b275853d1b675ea93801a55ee455fde2e9374a` (firmware PR569/570).
  Its actual first parent is published Dev032
  `2b50979c50a896dce367d17de72e6364818b940f`, satisfying the existing reviewed
  post-firmware source contract without changing Dev history or the validator.
- Target remains F7 / API88.23 / Dev009-032. No new firmware image or iOS patch.
- Automatic publication remains disabled. The user-authorized existing resumable
  publisher verified the bounded delta at the write boundary; no workflow
  permissions or protected audit verdicts were relaxed.
- No accepted-source/audit verdict is changed by this release plan.

The new receive-only **Envelope Check** is inside:
**Apps → Module One → Signals → TumoSpectrum → Envelope Check**.

Release the transmitter before entry or OK/CAL. Left/Right tunes 25 kHz, Up
selects INT/external CC1101, Down selects the next common band. Back releases the
radio. Pulsed / Flat carrier / Unclear describe RSSI amplitude only, not AM/FM
or protocol identity. The complete CC1101 floor down to -138 dBm is retained.

The app does not transmit, modify saved captures or delete user data. RF/BLE/SD
coexistence and device acceptance remain for user testing. Independent agent
review is not claimed.

Install using existing firmware009-032/API88.23 and Companion1.11.32:
refresh **FW Packages → Dev029 → Module One**. Other groups' payloads are
unchanged; the archive retains the complete cumulative Dev028 contents.
Open TumoSpectrum → Envelope Check after installation. Firmware flashing,
ESP32 flashing, Stable promotion and an iOS update are not part of this release.
