# Dev 026 — Quac hold/release and checked saves

Candidate plan only. The active catalog remains Dev 025 until verified
publication and a separate activation PR. Stable, existing protected-audit
decisions and automation remain unchanged. Do not run publication before the
target firmware release is public and its downloaded bytes have been verified.

- Exact firmware/package source: `fa3b71d8518a182909ea7c5834613752687bbb12`.
- Target: Tumoflip Dev 009-029, F7 API 88.22, private Sub-GHz ABI 1003 unchanged.
- Quac **0.12.1** adapts held/released frame completion and RAW EOF semantics,
  uses a 5 ms cancellable wait, and preserves Repeat/custom-button state.
- The FAP-linked save helper verifies a fresh backup before overwriting a signal,
  checks byte counts, truncate/flush/close and read-back, and attempts rollback
  while retaining the verified backup on failures. This is recoverable saving,
  **not atomic power-loss protection**. Pre-existing backups are not overwritten.
- Only `apps/Tools/quac.fap` may change from the immutable Dev 025 snapshot.
  Every other catalog member, including Weather Editor, must be byte-identical.
  The cumulative catalog delta retains earlier modified targets so skipped
  revisions do not lose the Weather update.
- Source API provenance is 88.22; this is not a full package baseline rebuild.
  Existing import checks and the real selected FAP must pass; do not add fake
  firmware exports to suppress incompatibilities.
- Keep automatic publication disabled. Build through the existing pinned
  Ubuntu 24.04 / toolchain 39 dry-run, then use the explicit native publisher
  only after re-verifying the artifact and predecessor. Verify remote bytes
  before and after publication, and activate through a separate reviewed PR.
- Replace the affected Pillow 12.3.0 build pin with **13.0.0**, retaining an
  exact CPython 3.12 Linux x86_64 wheel hash from PyPI. This addresses the five
  critical Pillow advisories reported on the old pin, including FPX/JPEG,
  multi-frame storage, TIFF writing and Ghostscript argument handling. The
  dependency is build tooling, not code shipped on Flipper. A regression rejects
  a return to the affected versions; existing unrelated alerts are not waived.

The paired firmware passed 1174 host tests, actual native FAT/FDX-B/Remote/Quac
regressions and the strict physical4096-byte C2 reserve gate. Those results are
not a substitute for native package provenance, downloaded-byte checks or
hardware acceptance. No iOS/ESP32 release or hardware installation is performed.

After installing Dev 009-029 and this package on owned equipment, check Quac
hold, release, RAW completion, Back cancellation and save/reopen on disposable
copies. Keep important original captures, configuration and keystores intact.
