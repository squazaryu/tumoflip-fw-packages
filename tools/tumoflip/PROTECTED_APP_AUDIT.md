# Protected app audit ledger

Tumoflip audits every exact `xMasterX/all-the-plugins` Community Pack before
TumoCompanion treats a protected binary difference as reviewed. A tag alone is
not an identity: the audit key is the tag plus the named SHA-256 of both ZIPs.

## Lifecycle

1. The twice-hourly run resolves the newest two packs and rolling Tumoflip
   release targets; a `tumoflip_release_published` repository dispatch starts the
   same reconciliation immediately, and a manual run may supply both exact pack
   tags. The workflow downloads current ZIPs by numeric GitHub asset ID, checks
   their size and SHA-256, resolves both source commits, and creates or reuses one
   canonical issue in `squazaryu/tumoflip-fw-packages`.
2. The scanner resolves every registered FAP by its declared pack and unique
   `archiveFileName`, then derives the live archive and `/ext/apps` routes. A
   category move therefore needs no registry edit; a missing, ambiguous, or
   noncanonical leaf fails closed. Explicit FAL families retain their exact
   family rules. The scanner also compares the original author ref. A full
   `protectedKeys` inventory makes a new protected FAP/FAL that lacks a registry route fail
   closed as an unregistered intersection.
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

## Source parity watcher

The Community Pack ledger is intentionally not the source-import ledger. A
protected app may live in an author repository (for example ProtoPirate) while
the Community Pack still contains unchanged bytes. The scheduled
`protected-source-parity.yml` workflow therefore checks the exact author ref
for every registry app and compares it with
`tools/tumoflip/protected_app_imports.json` in the pinned Tumoflip checkout.

Each import record binds four facts: the protected app id, its local source
path, the Tumoflip implementation commit that imported or adapted it, and the
exact upstream commit reviewed. The workflow fails closed and opens one
canonical issue when an author head advances, a `release-source` Community Pack
commit changes the protected app subtree, a source path is missing, or the
import manifest is incomplete. A release tag may advance for unrelated apps,
categories, or documentation: when the recorded `packSourcePath` is
byte-for-byte unchanged between the reviewed and current release commits, that
import remains verified and the report records the no-source-change explanation.
The protected package-audit firmware commits are pinned per channel in
`contracts/protected-audit-targets.json` (`implementations.stable` and
`implementations.dev`), so a moving `main` or `dev` branch cannot silently
change that audit input. The legacy singular `implementation` field is a
checked alias of the dev pin for schema-1 consumers; it is not a package
compatibility rule. Package evidence records the matching channel pin for
each stable/dev catalog target. The separate source-parity workflow keeps its
own exact implementation contract. This keeps audit provenance separate from
the catalog itself: if package bytes and manifests do not change, no new FW
Packages catalog or release is created.

The immutable history files are content-addressed by the semantic audit payload
(excluding only `generatedAt`), so a scheduled no-op never creates churn and new
target evidence never overwrites an older record. The cumulative `latest.json`
uses schema 2. Accepted
target entries contain `targetMD5s` and one or more unique provenance records for
each allowed hash. Stable and dev may legitimately prove the same target MD5.

The published entry dispositions are an audit schema, not an informal task
status. `sourceMatches`, `auditedDifference`, and `intentionallyReplaced` are
accepted only when the complete release audit is `verified`. A review decision
of `rejected` retains exact target evidence and is published operationally as an
`auditedDifference`; it is not an unresolved result. Source or author changes
without a matching reviewed decision remain unresolved and keep the canonical
issue open.

## Multi-surface protected features

`coverageSurfaces` records protected functionality that is intentionally split
across more than one Tumoflip implementation. The scanner validates and carries
this metadata into the immutable audit so downstream automation compares
capabilities instead of assuming that one upstream directory must map to one
local FAP.

ProtoPirate currently has two reviewed surfaces:

- Standard Sub-GHz owns radio-free RAW Auto Decode, Protocol Pack traversal,
  and restoration of the user's active pack;
- `applications_user/protopirate` remains the advanced ARF module with its own
  receiver lifecycle and radio-broker integration.

A package hash or route difference alone therefore cannot prove missing
ProtoPirate behavior. Any upstream change must be classified against the
specific surface that owns the affected capability.

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
