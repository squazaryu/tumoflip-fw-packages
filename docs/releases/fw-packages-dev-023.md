# FW Packages Dev 023 — Specter 3.3

For Tumoflip Dev 009-016 on F7 (API 88.14). In TumoCompanion select **FW
Packages → Dev 023**, install Specter, then run **Verify on device**. This is a
package-only prerelease; installing it does not flash a new firmware image.

The only changed SD payload since Dev 022 is
`/ext/apps/NFC/specter.fap`. Specter now keeps proximity feedback and clicks
consistent with the visible meter scale, offers a skippable intro, and labels
the logbook meter consistently. The v2 settings file is read as v3 without
resetting sensitivity, alerts, logging, or meter preference. Existing logs and
other user files are not removed.

Breaking changes: none. Manual migration: none. Stable FW Packages and
Tumoflip firmware are unchanged. Specter remains protected from Community Pack
replacement.

The exact Linux CI artifact passed native release verification against the
immutable Dev 022 predecessor. Its only changed target is Specter; all other
package payloads, cleanup rules, and data files are unchanged. Source commit
`42aad5d7e16cdb430fd7bbed1c37cf5164b3a334` has the published Dev 009-016
commit as its first parent. Host tests, APPCHK, and the native UI preview passed;
physical NFC behavior, settings migration on SD, intro navigation, and BLE
coexistence still require device testing before stable promotion.
