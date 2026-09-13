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

This plan authorizes an exact-source candidate build. Publication and catalog
index advancement follow successful verification of that candidate. Native UI,
transaction and build checks are separate from physical sensor/radio acceptance.
