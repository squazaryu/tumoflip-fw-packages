# FW Packages Dev027 — Device Library preparation

Prepare a Device Library1.1-only overlay over immutable Dev026. Pin exact
Tumoflip Dev009-030 source `6c07f477ea651541d9fcfa7f06c23d8a8ff43c85`, F7/API88.22,
and its locally verified manifest ID
`c01f032793dbeb73701fca61b4d89fd6bd0b553cb93ae30fb4a29c2b9b8d1b73`.
Firmware publication/download verification must complete before publishing this
independent package. This plan does not itself claim firmware availability.

Only `apps/Tools/device_library.fap` may change. Keep the history FAL/private ABI2,
all other119 catalog members, cleanup entries and all user data unchanged.
Do not rebase the entire catalog onto the newer firmware snapshot. No new app
allowlist or automatic publisher permission is introduced. Current release,
lineage/index, protected-audit dispositions and the Stable channel remain
unchanged in this preparation PR.

Install matching firmware/native resources first, then the Base group in Dev027.
The wallet's RFID/iButton inspect routes fail closed if those drivers are old.
Old cards are read without rewriting; explicit Save writes TDC2 revisions.
Device Library1.0 cannot read new revisions after downgrade, so keep a card-data
backup. Removing a link/pin never deletes its original capture.

Native dry-run, exact bounded-delta verification, explicit draft/public downloaded
byte checks and a separate activation PR are still required. Physical-device
acceptance is pending and is not asserted by tests or this plan.
