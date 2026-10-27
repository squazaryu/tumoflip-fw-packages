# FW Packages Dev029 — TumoSpectrum Envelope Check

Status: authorized candidate plan; not yet published or selected by the catalog.

- Update only `signal_workbench` / **TumoSpectrum 3.5** in **Module One**.
  Keep every other Dev028 payload byte-identical; no path is removed.
- Exact package source: `d8b4500ccf97014b079d2161f7d98b39d33978aa`.
  This single-parent package snapshot has the exact tracked tree of reviewed
  Dev `a6b275853d1b675ea93801a55ee455fde2e9374a` (firmware PR569/570).
  Its actual first parent is published Dev032
  `2b50979c50a896dce367d17de72e6364818b940f`, satisfying the existing reviewed
  post-firmware source contract without changing Dev history or the validator.
- Target remains F7 / API88.23 / Dev009-032. No new firmware image or iOS patch.
- Native Ubuntu24.04/toolchain39 build, bounded delta and immutable publication
  must pass before a separate catalog activation. Automatic publication remains
  disabled; use the existing resumable publisher only on the verified artifact.
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
