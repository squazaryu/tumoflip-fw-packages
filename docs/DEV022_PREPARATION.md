# FW Packages Dev 022 — preparation only

This plan prepares a narrowly scoped overlay release from the exact published
Tumoflip Dev 009-012 source:

- firmware tag: `t-dev-009-012`
- firmware commit: `9531d28b88970df05423c538cefd7966bd08e8af`
- firmware release ID: `395831475`
- package manifest release ID: `ed024a4619b45fb21cc8e6ea39a48e135027f381a731c2d54b8ba16ff9a94912`
- API / target: `88.14` / F7 (`7`)
- predecessor catalog: `fw-packages-dev-021`, manifest release ID
  `4025cc8b2cc780dc1415e90ebee325f1002e6018a3431253816c3f8a8be61dcb`

The plan selects only these source-built replacements:

- `apps_data/arf_subghz_full/packages/capture_inspector.fap` — Capture Inspector 1.1
- `apps/Module One/Signals/signal_workbench.fap` — TumoSpectrum 3.2

Everything else in Dev 021, including cleanup entries and user data, must remain
byte-identical. This is an independent overlay revision, not a firmware snapshot
or a new package baseline. `current-releases.json`, `catalog-lineage.json`, and
`catalog-index.json` remain unchanged in this preparation PR. Publication and
catalog activation are separate steps after the exact candidate archive has been
built and independently verified.

Physical-device acceptance remains pending. Dev 022 is intended to make these
updated FAPs available for testing with Tumoflip Dev 009-012; no device test is
claimed by CI or by this plan.
