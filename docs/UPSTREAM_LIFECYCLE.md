# Upstream lifecycle evidence

The upstream lifecycle collector is a read-only evidence layer. It answers two
different questions without conflating them:

1. What did the upstream repository accept, reject, test, and release?
2. Is that evidence sufficient to create a Tumoflip implementation review task?

It never imports code, advances a reviewed boundary, creates a firmware release,
or claims physical-device acceptance.

## Task eligibility

| Upstream state | Evidence result | Tumoflip task disposition |
| --- | --- | --- |
| Pull request open or draft | `pending` | `suppressed` |
| Open pull request removed from the tracked milestone | `deferred` | `suppressed` |
| Pull request closed without merge | `declined` | `suppressed` |
| Pull request merged, exact PR and branch checks passed, merge remains on tracked branch | `accepted` | `eligible` |
| Pull request merged but a required PR or branch check is pending, failed, missing, or contradictory | `blocked` | `blocked` |
| Merged pull request already contained in the reviewed boundary | `accepted` | `suppressed` as `alreadyReviewed` |
| Issue open | `pending` | `suppressed` |
| Issue closed as `not_planned` | `notPlanned` | `suppressed` |
| Issue closed as `completed` | `completed` | `suppressed` until exact implementation evidence exists |

This means a rejected upstream proposal cannot create a new Tumoflip
implementation task. An issue resolution alone is also insufficient: the
collector requires an exact accepted commit or pull request.

## Exact evidence

For GitHub repositories the report binds:

- repository and target branch;
- exact milestone number, title, state, complete item list, and open/closed counts when configured;
- PR number, head SHA, merge SHA, update and merge timestamps;
- PR milestone membership and current mergeability/conflict state;
- latest review state per reviewer;
- configured check-run or status-context names on the exact head SHA;
- reachability of the merge SHA from the reviewed boundary and current branch;
- reachability from the latest release tag when release tracking is configured;
- issue number, state, and `state_reason`.

Required check names are contract data. A missing or renamed required check is
not silently accepted. Network, API, pagination, identity, or contradictory
state failures remain fail-closed.

## Repository coverage

- `DarkFlippers/unleashed-firmware`: full GitHub lifecycle, exact `f7 firmware`
  check, release inclusion, and the complete current `unlshd-093` milestone.
- `D4C1-Labs/Flipper-ARF`: GitHub lifecycle and exact `build` check. Release
  inclusion is not yet part of its source contract.
- ProtoPirate: exact Git ref monitoring. Its provider API is not currently a
  reliable automation surface, so lifecycle capability is reported as
  `gitOnly`; no PR or issue state is inferred from a branch SHA.

## Acceptance boundary

`eligible` is corroborating upstream evidence, not final Tumoflip acceptance.
Any selected integration still needs its normal local review, build and tests,
API/C2 and package checks, and hardware acceptance where applicable.
