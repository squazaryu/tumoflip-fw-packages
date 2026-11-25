# FW Packages Dev036 — RAW Edit 1.10

Dev036 is an independent package-only update based on the immutable Dev035
archive. The complete ZIP retains all 143 paths and the same 34 cleanup rules;
only `apps/ARF Tools/subghz_raw_edit.fap` changes.

The new RAW Edit shows both ends of long capture names without overlapping the
duration. Its loading screen remains visible until initial markers and the
overview are ready. The direct-open path, save behaviour, API and app category
stay unchanged. Upstream author and license are preserved.

This release uses the supported `delta` install scope: the full archive remains
available for verification, while the managed install surface is just RAW Edit.
It does not require an exact firmware-version match. Use TumoFlip F7 firmware
with the required exported API; the intended existing baseline is Dev 011-004
(API 88.23). A new firmware or TumoCompanion release is not required.

TumoCompanion compares the selected file's device checksum to the manifest.
An unchanged 1.9 installation is offered one update; an already matching 1.10
installation needs none. Other applications and user captures are not changed.
No new cleanup entry or automatic file migration is introduced.

Before activation: verify exact-source CI, standalone FAP imports, full
archive/manifest hashes, the 143-file inventory, 142 byte-identical predecessor
payloads, and the released TumoCompanion manifest projection. Physical-device
UI acceptance remains a Dev test, not a claimed result of the host checks.

Published at [fw-packages-dev-036](https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-036),
release ID 457795305. Native Linux build
[45570449217](https://github.com/squazaryu/tumoflip-fw-packages/actions/runs/45570449217)
passed at source `8e89b9b1e390614c64629ab415f77b18cb6f2a54` and publisher
`07163d59b48191104d2830feaca62143cd6d4f6a`. All four public assets were
downloaded anonymously and byte-compared. The released TumoCompanion 1.12.0
model validated all 143 entries, exposed one managed FAP, and accepted Dev
channel/API/target compatibility without an exact version check. Stable and
all historical catalogs are preserved by the separate activation.
