# FW Packages Dev028 — Field Kit

Published: https://github.com/squazaryu/tumoflip-fw-packages/releases/tag/fw-packages-dev-028

- Firmware target/source: Dev009-032, F7 local API88.23,
  `2b50979c50a896dce367d17de72e6364818b940f`.
- Publisher: `3e431d18901ba40c650da5b34926e9fe498b8a0d` (PR180).
- Native Ubuntu24.04/toolchain39 CI40356526999 passed; separate local
  bounded-delta/provenance verification passed.
- Updates Device Library1.2, TumoSpectrum3.4 and ARF Status0.4.3. Adds Field
  Tools, experimental Passive NFC Sniffer and ten receive-only protocol leaves.
- Exactly15 payloads changed/added;117 unrelated predecessor payloads remain
  byte-identical, none removed. All132 entries are SHA/size/path checked.
- Cumulative independent delta retains earlier Quac/Weather updates. The
  pre-existing firmware-owned RX Link is not newly moved into this catalog.
- New manifest ID:
  `e85214ff44953881965bbccec5ae06386d5f5dc38b09d064d842ece8c50dd979`.
- All4 remote assets were verified while draft and after publication, then
  freshly downloaded and byte-compared with the CI artifact. Automatic native
  publication remains disabled. Existing audit verdicts are not marked accepted.

Install Companion1.11.32, then firmware009-032 including native resources,
then refresh FW Packages and install Dev028's affected Base/Module One/ARF/
Protocol Pack groups. No automatic device/ESP32 flash or Stable promotion.

Old cards remain readable. Portable imports create separate copies; an
inventory cannot restore lost bytes. Copy exports off SD via a reader or USB
SD mode; Companion Files supports upload, not generic download. No user-data
family is overlaid and these new tools do not delete original recordings.

Physical-device acceptance remains pending. Passive NFC is experimental; no
key-recovery/relay/new automotive-transmit functionality is added. Independent
agent review is not claimed.
