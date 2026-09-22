# Community Pack 21sep2026 source and ABI review

Source a80f75c6705bd709d0c6f922e84eb940f99730fc; API 88.12.
Compared with 19sep2026 (8970b6ba0e48a9c63b4bfaf07ca121f5b08e5b34).
The downloaded base/extra SHA-256 values match protected audit issue #121:

- base: 044abf60b6c9fc155f72a768708fa890e609d7bdd700022c09044a0de8aa2fc1
- extra: c5287243dcefcd0d0258489e1082bf3c074ce70e4e9955a7a8390e2e0d233da2

## ABI result

Against Tumoflip 009-006 (756187d03e, API 88.12): 420 binaries, including
363 FAP, 57 FAL and 43 embedded binaries across 33 asset bundles.
417 pass import/host checks; three have missing upstream GPS service imports:
nearby_files, subghz_wardriving and gps_rpc. The first two have our separately
managed implementations; the Community binaries must not replace those routes.
GPS RPC remains incompatible. No new firmware exports are justified by this audit.

The previous exact host contract produced 41 additional unverified-host findings
because TOTP, Metroflip and LAN Tester were rebuilt. For all three, the exact
upstream exported-API source SHA-256 is unchanged. The separate dated host
contract binds the new binary SHA-256 values to those same exports; the scanner
also verified every declared export exists in its own host binary. The original
19sep contract is preserved. This proves imports, not runtime behavior or acceptance.

Reproduce with tools/community_abi_audit.py, both release ZIPs, the 009-006
targets/f7/api_symbols.csv, GNU arm-none-eabi-nm and
--host-contract contracts/community-plugin-hosts-21sep2026.json.
Full normalized result: community-21sep2026-abi.json.

## Source disposition

- Bounce and Stack Attack are new Community games, with no core integration
  required. Their FAP imports pass. No on-device gameplay acceptance is claimed.
- FlipCrypt 0.7 changes cipher organization, adds classical ciphers/Base16 and
  increases stack to 8 KiB. Imports pass. Saving moves from /ext/flip_crypt_saved
  to apps_data/flip_crypt/saved, while the About text still names the old path;
  no migration was found. Storage helpers still ignore write results, accept
  unchecked allocation/read lengths, and the Save scene reports Saved regardless
  of failure. These existing error-handling problems remain a separate app review;
  API compatibility is not an assertion of reliable saving.
- ProtoPirate adds Honda 315 MHz, custom-model parser rewind and host callbacks
  for the Config plugin. Our bounded shared RX table takes only the Honda profile.
  The upstream startup path still ignores failed config/model loading and can
  dereference a missing preset. Keep the customized protected implementation.
- Other protected source directories have no changes in this pack range.
  Earlier author-source/hardware questions (including Marauder) remain separate.
  SubBrute/Agentic replacement decisions were not changed.

## Acceptance boundary

This report and artifact-pinned host contract do not mark #121 verified, advance
protected acceptance, change installed catalogs, or publish a package. The full
archive import scan is complete; independent rebuilds and hardware tests of all
363 FAP have not been performed.
