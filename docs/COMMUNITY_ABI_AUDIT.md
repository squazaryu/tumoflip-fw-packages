# Community Pack ABI audit

`tools/community_abi_audit.py` checks every `.fap` and `.fal` in the exact
`all-the-apps-base.zip` and `all-the-apps-extra.zip` archives against the
`targets/f7/api_symbols.csv` from the exact Tumoflip `dev` checkout.

The firmware loader currently gates only the API major. The audit therefore
also reads each ELF's undefined symbols:

- standalone FAPs may import only the exported Tumoflip firmware symbols;
- FALs may additionally import symbols exported by an FAP in the same pack,
  which covers host-plugin families such as TOTP;
- missing imports produce `needsReview` and a canonical review issue. The
  workflow never installs or executes a Community Pack binary.

The report's `summary.by_disposition` separates missing firmware imports from
host-contract review, missing FAL ownership, plugin imports, and API/target
mismatches. `host_contract_review` means the host binary or its declared exports
must be reviewed against the pinned contract; it does not assert that the FAL
is broken. A changed host hash is never accepted automatically.

The archives are checked for exact release digests before scanning, and the
scanner bounds archive size, member size, member paths, binary count, and
duplicate ZIP names. A report is kept as an artifact for 14 days and the
canonical issue is updated only after its ownership is revalidated.

Local example:

```sh
python3 tools/community_abi_audit.py \
  --base-archive all-the-apps-base.zip \
  --extra-archive all-the-apps-extra.zip \
  --api-symbols ../tumoflip/targets/f7/api_symbols.csv \
  --nm llvm-nm \
  --output community-abi-audit.json
```

An ABI-clean result is necessary for a Community Pack release, but it does
not replace protected-source parity, package hash verification, or hardware
acceptance of radio/NFC/ESP32 functionality.
