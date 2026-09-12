# Weather Editor package preparation

Source: `77ca83763507a3098182e32cb01cfa8135e373dc` in Tumoflip (Dev 008-028).
Export: `apps/Sub-GHz/weather_editor.fap`, group `base`, package-only.
Source preview: `handoff/weather-editor-native-ui/after/comparison.png` and
`handoff/weather-editor-native-ui/after/all-screens.png` in that source tree.
The preview uses production draw callbacks, U8g2 fonts and controlled models.

The published predecessor is `fw-packages-dev-015`. Its payloads, including
Nearby Files, must be preserved by the eventual `fw-packages-dev-016` overlay.
The earlier local weather dev-015 plan was based on a stale checkout and is
not a release input. Do not publish, install or promote its generated ZIPs.

The dev-016 plan selects only Weather Editor and preserves every dev-015
payload. Install Tumoflip Dev 008-028 before this package: it supplies API 88.7
and the common TextInput and scrolling-message fixes validated with this FAP.
The inherited API 88.0 catalog baseline is historical compatibility metadata,
not a claim that Weather Editor can run on that old firmware. The FAP build's
import check runs against the exact 008-028 source SDK.

This is a Dev functional-test release. Physical sensor RX/TX, external-radio
disconnect/reconnect and device installation/navigation remain acceptance
checks for the user. Native UI rendering, input/navigation sanitization,
transactional save/load tests and software builds are completed separately.
