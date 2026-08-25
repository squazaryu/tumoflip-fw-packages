# ESP installer manifest audit

The ESP Flasher `Flash Package` path is intentionally separate from manual
firmware flashing.  The scheduled `Audit ESP installer manifests` workflow
selects the newest non-draft, non-prerelease Marauder release and accepts only
one of the two upstream carriers:

- `firmware-manifest.json` (manifest-only evidence; segment bytes stay
  unverified and therefore require review);
- `marauder-installer-assets.zip` (manifest plus every referenced segment).

For every target the audit records the upstream release ID and tag, source
commit, carrier digest, extracted manifest digest, board key, recipe, and the
canonical role/offset/size/SHA-256 segment list.  It rejects unsafe ZIP paths,
duplicate roles/files, overlapping segments, missing binaries, size/digest
mismatches, non-authoritative manifests, and raw BIN input without a manifest.

`contracts/esp-installer-baseline.json` is a checked-in identity ledger for
stable tags.  The workflow compares the release ID, source commit, carrier
digest, and manifest digest with that ledger.  A changed asset under an
existing tag, or a newly observed tag, is reported as `needsReview`; it can
never silently replace the previous installer candidate.

The result is fail-closed:

- `verified` is possible only after the board profile is explicitly accepted
  in `contracts/esp-installer-audit.json` and all per-board hardware checks
  (`board`, `flash`, `boot`, `smoke`) are recorded;
- `needsReview` is an observation, not an install authorization;
- `rejected` is invalid evidence.

The current C5 and Marauder v6.1 profiles are deliberately `needsReview`.  In
particular, the upstream four-file C5 recipe with a 20,784-byte bootloader is
not treated as the accepted Tumoflip three-file 20,464-byte profile.  The
workflow never publishes a FW Package, changes ESP Flasher code, or makes
Manual Flash unavailable.  A human review must update the policy and record
hardware evidence before any package publication is considered.
