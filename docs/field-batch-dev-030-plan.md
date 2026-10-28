# Initial field-batch content plan — historical preparation

Dev030 is now published; see [release and activation record](releases/fw-packages-dev-030.md).
The text below records the pre-publication boundary, not the current catalog head.

The firmware implementation branch `feat/field-batch-20261028` prepares five
new Base binary overlays: `servo_tester_plus`, `power_journal`, `sensor_watch`,
`tumo_varta`, `tumo_uhf_inventory`; the existing `device_library`1.3 and
`signal_workbench`3.6 hosts add deferred Sensor Watch routes. Source API stays
88.23. The current published/active catalog remains Dev029.

Ownership/allowed binary paths are registered here. No release plan is filled
with an unmerged/provisional source SHA, no accepted audit pins are advanced,
and no current-releases/index/release assets are changed. After native UI review
and exact merged-source CI, prepare the next immutable Dev030 plan against its
accepted firmware identity and source snapshot using the existing publisher.
Verify every unchanged member against Dev029 and account for every changed/new
member; never infer the intended delta solely from the total file count.

Ordinary Community Varta, UHF Expansion and gbhobbytech Servo Tester+ keep
their separate IDs/paths. They are not silently replaced or removed. Sensor and
Power history/CSV directories under `apps_data/device_library/` are user data,
already protected as a family and never binary/data overlays.

The Renode/Flipper emulator is not a package payload: model license/runtime
readiness and actual boot validation remain unresolved. No iOS app binary or
ESP32 backend is included by this ownership-only change.
