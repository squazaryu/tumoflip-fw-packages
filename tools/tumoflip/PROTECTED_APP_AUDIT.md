# Protected app audit ledger

Tumoflip audits every exact `xMasterX/all-the-plugins` Community Pack before
TumoCompanion treats a protected binary difference as reviewed. A tag alone is
not an identity: the audit key is the tag plus the named SHA-256 of both ZIPs.

## Lifecycle

1. The scheduled run resolves the newest two packs, or a manual run supplies
   both exact tags. The workflow downloads current ZIPs by numeric GitHub asset
   ID, checks their size and SHA-256, resolves both source commits, and creates
   or reuses one canonical issue in `squazaryu/tumoflip-fw-packages`.
2. The scanner compares every registered source path and the original author
   ref. A full `protectedKeys` inventory makes a new protected FAP/FAL that lacks
   a registry route fail closed as an unregistered intersection.
3. Reviewed and unresolved entries are merged into a strict cumulative schema-2
   ledger. The authoritative predecessor is the latest verified immutable audit
   release (or the content-addressed bootstrap for genesis), never the mutable raw
   branch. Every release binds its predecessor tag, numeric release ID, tag
   commit, ledger digest and provenance digest. The resolver rejects gaps, forks,
   or historical rewrites before allocating the next
   `audit-ledger-YYYYMMDD-NNN` revision. An exact semantic rerun reuses the
   existing immutable head; a changed target audit for the same Community Pack
   receives the next revision. Only after immutable proof succeeds may the
   workflow fast-forward the transitional `protected-app-audit-ledger` mirror.
4. A target-bearing entry is accepted only when its target bytes occur in an
   exact released FW Packages manifest *and* the corresponding ZIP. The scanner
   checks path, byte count, MD5, SHA-256, clean source commit, revision and tag.
   A newer catalog may additionally retain a bounded older overlay through
   `compatible_builds`; the scanner recomputes the current manifest `release_id`
   and admits only aliases tied to one exact older catalog identity. Undeclared
   files from that older catalog remain untrusted.
5. Changed source needs an exact decision. A port records author and pack source
   commits, changelog, implementation commit, FW Packages channel/revision/tag,
   and hardware acceptance. Rejected changes still pin the exact retained target
   bytes. An intentional replacement is the only accepted missing-target case.
6. A separate least-privilege job re-downloads the immutable release, compares
   the raw ledger byte-for-byte, and only then reconciles the issue. The issue
   remains open while any artifact is unresolved; only a fully verified scan
   closes it automatically. Status and issue identity are read from the verified,
   release-bound audit rather than from an untrusted inter-job summary.

The immutable history files are content-addressed by the semantic audit payload
(excluding only `generatedAt`), so a scheduled no-op never creates churn and new
target evidence never overwrites an older record. The cumulative `latest.json`
uses schema 2. Accepted
target entries contain `targetMD5s` and one or more unique provenance records for
each allowed hash. Stable and dev may legitimately prove the same target MD5.

## Reviewed decision example

```json
{
  "schema": 2,
  "decisions": [
    {
      "appId": "subghz_raw_edit",
      "throughAuthorCommit": "<40 lowercase hex>",
      "sourceCommit": "<40 lowercase hex>",
      "disposition": "auditedDifference",
      "changelog": "Ported the reviewed upstream behavior.",
      "implementationCommit": "<40 lowercase hex>",
      "fwPackages": {
        "channel": "dev",
        "revision": 5,
        "releaseTag": "fw-packages-dev-005"
      },
      "hardwareAccepted": true
    }
  ]
}
```

Run the local gates with:

```sh
python3 tools/verify_catalog.py contract --root .
python3 tools/audit_bootstrap.py
python3 -m unittest discover -s tests -v
```
