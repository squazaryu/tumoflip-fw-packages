# Protected FAP automation

The repository now treats a protected application as a provenance chain rather
than a path or a package snapshot:

```text
upstream ref
    -> Tumoflip implementation commit
    -> built FAP / firmware resource
    -> FW Packages or firmware release
    -> device hash and hardware acceptance
```

## Automated lanes

| Lane | Workflow | Purpose | Can publish code? |
| --- | --- | --- | --- |
| Community Pack bytes | `Protected App Audit` | Exact archive, route, source MD5 and target provenance | No |
| Imported source parity | `Watch protected source parity` | Checks every registered upstream app and release-source path | No |
| Tumoflip surface drift | `Watch Tumoflip implementation drift` | Checks current `main`/`dev`, protected paths, new application roots and stale audit pins | No |
| ARF / ProtoPirate refs | `Watch ARF and ProtoPirate sources` | Watches the repositories that previously escaped the generic Unleashed watcher | No |
| Unleashed upstream | `Watch Unleashed upstream` | Records the human-review boundary for upstream firmware changes | No |

Every lane is fail-closed. A changed source, unavailable ref, unknown
`applications_user` root or stale audit pin creates/updates a canonical issue;
the baseline is never advanced by the scheduled job.

## Surface inventory

`contracts/protected-surface.json` contains the reviewed implementation refs and
the list of Tumoflip-owned source roots. The upstream roots are derived from
`tools/tumoflip/protected_apps_registry.json`, so an application cannot be
silently removed from the source-parity registry.

The drift report distinguishes:

- `protectedChanges` — a tracked protected source changed;
- `reviewChanges` — an NFC, Sub-GHz, loader, target or user-app path changed;
- `unclassifiedRoots` — a new application directory has no owner/source class;
- `removedRoots` — a reviewed application root disappeared;
- `auditPins` — the immutable audit contract is compared to the reviewed branch;
  `current`, `behind`, `ahead`, `diverged` and `unavailable` are all explicit,
  and every state except `current` blocks silent acceptance.

The registry and surface contract are also validated as repository-relative
`applications_user/` paths. A malformed path, duplicate app identity, or
missing source root fails before any issue reconciliation, rather than being
interpreted as an empty scan.

## Human decision boundary

Automation may identify a candidate, build and hash an artifact, and attach
evidence to an issue. It must not merge an upstream port, advance a source
baseline, publish a package or claim hardware acceptance. Those actions require
an explicit reviewed commit and, for radio/NFC applications, the normal Flipper
hardware acceptance gate.

When a candidate is accepted, update the corresponding contract in a reviewed
PR, run the full validation suite, and only then close the canonical issue.
