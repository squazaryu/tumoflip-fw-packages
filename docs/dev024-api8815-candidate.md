# Dev 024 preparation — full API 88.15 baseline

This plan is a candidate, not publication or hardware acceptance. Active catalog
stays Dev 023; stable, audit dispositions and automation remain unchanged.

Firmware source is pinned to 9adb4fe71dd962811aea92de030c1c4bd35a4b15 / 009-018.
The paired snapshot is fully rebuilt/validated at API 88.15, including the checked
packet API and private radio-device ABI 1003. Marauder includes passive Remote ID
commands; the external board still needs a Remote ID-capable firmware build.

Use baseline mode, not a small overlay over API 88.14. Do not reuse the older
external CC1101 driver descriptor. Source/target source identity and ZIP/manifest
hashes must be verified before publication; existing Dev 023 bytes stay immutable.
Owner UI preview and firmware/iOS CI are still required; no release should be
published while their draft PR checks fail or required visual checks are pending.
