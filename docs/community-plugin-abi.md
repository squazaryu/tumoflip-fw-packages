# Embedded Community ABI checks

The auditor inspects top-level FAP/FAL binaries and recursively reads their
`.fapassets` (ELF/F7 manifests and internal bundle MD5 checksums). Downloaded code
is never executed. Resource paths, section tables, sizes, bundle depth, total
uncompressed bytes and binary counts have explicit limits. Ordinary repeated ELF
section names are allowed; duplicated manifest/assets sections are rejected.

FAPs may import firmware exports only. Embedded FALs belong to their containing
FAP; external FAL ownership is explicit in `contracts/community-plugin-hosts.json`.
Private imports are permitted only if the exact host binary SHA-256 matches that
contract, the names are declared in its own reviewed host API, and its symbol
table defines them. Another FAP in the same archive cannot satisfy the imports.
Undefined weak symbols are not definitions.

The initial contract records TOTP, Metroflip and LAN Tester from Community Pack
19sep2026 / `8970b6ba0e48a9c63b4bfaf07ca121f5b08e5b34`. A changed host with private
imports fails closed until its contract is reviewed. Do not auto-advance hashes.
These bindings are ABI facts, **not protected-app acceptance decisions**.

The schedule, LLM monitoring prompt, SubBrute/Agentic Remote decisions and hardware
acceptance are untouched. No audit ledger was published. The separately approved
Specter delivery adds its protected registry route and source provenance, without
advancing the unrelated protected-audit acceptance pins.

Run:

```sh
python3 -m unittest discover -s tests
python3 tools/community_abi_audit.py \
  --base-archive all-the-apps-base.zip \
  --extra-archive all-the-apps-extra.zip \
  --api-symbols /path/to/firmware/targets/f7/api_symbols.csv \
  --nm arm-none-eabi-nm --output community-abi.json
```

Exit 0 means no missing imports/ownership findings; 1 means review findings; 2
means the input could not be safely inspected. None proves physical behavior.
The unchanged workflow picks up the default checked-in host contract.

Validation: 279 control-plane tests pass; 13 are focused ABI tests. Python stdlib
trace with `--count --summary --missing` reports 84% auditor and 86% bounded ELF
parser line coverage. Exact 19sep2026 archives contain 418 binaries after expanding
43 embedded plugins (33 bundles); 415 pass static checks. The existing Nearby
Files/Wardriving/GPS RPC missing GPS-stream imports remain findings, not waived.
