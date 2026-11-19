# FW Packages Dev033 — exact Dev011-002 snapshot plan

This reviewed plan binds the independent Dev033 catalog to the complete native
package snapshot from firmware `t-dev-011-002`, commit
`1867a7e6774dcf180e2164ccbb735b4526c5922c`, API 88.23, F7. The pinned manifest
ID and asset hashes are taken from the exact merged-source build and must be
checked again against the published firmware assets before package publication.

The immutable Dev032 catalog is the predecessor; its 143 package files and
archive/checksum were verified. No dictionary overlays are present there. The
snapshot maintains 143 paths and the existing cleanup list. The four ARF runtime
modules that include the shared settings loader receive the ignore-filter fix;
the ARF menu hub is unchanged. Existing signals and user `apps_data` are not
deleted or moved by this plan. No new protocol or attack feature is introduced.

This plan does **not** publish or activate a catalog. Independently verify the
firmware and all four catalog assets before publishing; then separately update
the current release and append-only rollback index. Keep Stable007, Dev032,
older catalog entries and existing protected-audit decisions unchanged. The
current Community protected-source review is not declared green by this plan.

No new iOS build or ESP32 flash is needed. The Flipper/Gallagher/Feron hardware
acceptance remains outstanding and is not implied by successful builds.
