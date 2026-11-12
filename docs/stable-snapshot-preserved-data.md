# Stable snapshot data preservation

A full Stable firmware snapshot can omit data-only dictionaries previously
delivered by an independent catalog. A catalog may preserve those exact bytes
by declaring `preserveDataOverlays` in its exact `firmwareSnapshot` release plan.
Only names already in `allowedDataOverlays` are accepted. This is not permission
to read arbitrary local files or add new data from the control checkout.

Every retained entry must exist in the verified immutable predecessor, use the
contracted group and `/ext/<source>` target, have kind `dictionary`, and retain
`preserve_existing=true`. A conflicting native snapshot entry is an error.
Unselected files are never inherited by this mechanism.

Native payload bytes remain exactly those of the verified firmware snapshot.
Only declared predecessor data is appended; archives are normalized and checked
against the composed manifest. Verification independently reconstructs the
archive and its digest. Provenance reports preserved sources and the actual
changed-source set, not a fictitious removal of retained data.

Existing snapshot plans without this field keep the original byte-identical ZIP
behavior. Baseline/overlay/data modes cannot use the field. This does not advance
Community protected audit acceptance, change firmware, delete SD files, or
authorize replacement of a user's modified dictionary on the device.
