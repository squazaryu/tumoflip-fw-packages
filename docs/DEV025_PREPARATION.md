# Dev 025 — Quac and Weather preset guards

Candidate plan only. Current catalog stays Dev 024 until verified publication
and a separate activation PR. Stable, protected-audit decisions and automation
are unchanged.

- Exact firmware source: `69eb0bd34a69630eec76cabeb638da31c09fa0df`.
- Target: Tumoflip Dev 009-025, F7 API 88.20, private Sub-GHz ABI 1003 unchanged.
- Quac 0.12.0 rejects missing modulation presets before starting radio activity.
- Weather Editor 2.5 rejects missing receive/transmit presets instead of reusing
  stale settings. User weather recordings and configuration are untouched.
- Only `apps/Tools/quac.fap` and `apps/Sub-GHz/weather_editor.fap` may change.
  Every other predecessor catalog member must remain byte-identical.
- No new firmware-only API imports are introduced by these two guards. Existing
  API-major compatibility is preserved; no wholesale baseline rebase is needed.
- Build on the pinned Ubuntu 24.04 / toolchain 39 workflow, then independently
  verify the bounded delta and downloaded release assets before activation.
- The paired firmware candidate passed 1128 tests and its strict release gate:
  physical C2 reserve 4096 B, DfuSe C1 end 0x080D5E50. That does not substitute
  for the separate native package build/provenance and device acceptance.

After installation on owned equipment, check Quac and Weather normal start/stop,
Back/cancel and a disposable file/preset mismatch. Never test by deleting user
captures or replacing their keystore. No hardware acceptance is claimed here.
