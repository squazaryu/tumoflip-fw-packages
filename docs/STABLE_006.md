# Stable package revision 006

Promote the package-only Quac, Nearby Files and Weather Editor applications
from Tumoflip Stable 008 (`e2cf3ba8fa96f36d05b86cee973ea36d0e55403d`).
Build only these three exports on the pinned Ubuntu 24.04/toolchain 39 runner.
Preserve all other stable-005 payloads, including its additive dictionaries,
byte-for-byte. No user application data directory is package-owned.

Install Stable 008 (API 88.7) before these applications. The catalog's inherited
Stable 007/API 88.4 baseline is immutable provenance, not the SDK against which
the three new FAPs were built. The package stream remains independent; do not
replace it with a full firmware snapshot or an empty reference baseline.

Published as `fw-packages-stable-006`, built in workflow `34745090570` from
publisher `5a823f8149a2437fb2063d2e5739ed1e6b117b0f`. Independent verification
confirmed exactly three changed FAPs and preservation of every other payload.
Publication verified the remotely downloaded asset bytes before and after
publishing. The catalog index records this immutable release; the next stable
revision is 007. Native UI, transaction and build checks remain separate from
physical sensor/radio acceptance.
