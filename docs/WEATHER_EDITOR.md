# Weather Editor package preparation

Source candidate: `fd9391a40290eda1c6d9aa6925bdafc9c17a56af` in Tumoflip.
Export: `apps/Sub-GHz/weather_editor.fap`, group `base`, package-only.
Source preview: `handoff/weather-editor-native-ui/after/comparison.png` and
`handoff/weather-editor-native-ui/after/all-screens.png` in that source tree.
The preview uses production draw callbacks, U8g2 fonts and controlled models.

The published predecessor is `fw-packages-dev-015`. Its payloads, including
Nearby Files, must be preserved by the eventual `fw-packages-dev-016` overlay.
The earlier local weather dev-015 plan was based on a stale checkout and is
not a release input. Do not publish, install or promote its generated ZIPs.

This change adds only the build capability. A final release plan is pending
radio/installation acceptance and reconciliation with the firmware carrying
the common TextInput cursor-buffer fix (`dc9d80c3ad` on the source branch).
The existing API 88.0 catalog baseline does not by itself prove compatibility
with every import used by a FAP built against API 88.7. Verify actual imports
and firmware prerequisites before publication. No source or release pin is
advanced here, and no new release is published.
