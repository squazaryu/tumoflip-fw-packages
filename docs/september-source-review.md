# September Community Pack source review

Reviewed 2026-09-08. This document does not authorize installing upstream binaries.

- Marauder: author d9f063b36e / ed6d2b8147 is selectively implemented in
  Tumoflip Dev 008-016. New author e39f24bdba removes GPS Tracker and decreases
  NUM_MENU_ITEMS. Neither GPS Tracker nor gpstracker exists in current Tumoflip,
  so this latest change needs no port. Hardware GPS/FindMy acceptance is pending.
- Quac: a46cffc15f remains author HEAD. Lifecycle fixes and the non-secure-only
  Picopass implementation are present in Dev 008-016/FW Packages Dev 014;
  upstream secure/Loclass behavior is intentionally not imported. Hardware
  acceptance is not recorded and must not be synthesized to close an audit.
- Claude Remote: the ARF main rewind does not change its source subtree.
  The replacement decision is recorded against exact 7sep2026p2 source bytes
  and author HEAD. This is not a change to the separately maintained Claude Buddy.
- Wardriving 9487010090 / 198c6848ba: lazy RX pipeline allocation is a useful
  candidate, but allocator failures, callback lifetimes, radio stop/join and
  re-entry require tests. The private worker halves the sample buffer from
  4096 to 2048; acceptance must measure overruns with GPS/hopping contention.
  The GPS Taylor approximation fails across the date line: at latitude zero,
  longitude 179.9 to -179.9 produces about 932.36 km instead of 22.24 km because
  the half-longitude delta is outside the documented approximation range.
  Do not copy the approximation or claim its error bound. Wardriving remains
  unported; normal protected-package byte verification is a separate matter.

Package audit inputs still pin implementation snapshots and package revisions
older than Quac Dev 014. Resolving changed-source decisions requires exact
published target provenance and real hardware evidence, not only updating a
reviewed-author hash. Pending audits must remain pending until those conditions
are met. Minor API 88.6 versus 88.5 alone is not proof of incompatibility;
imported symbols and the actual loader checks determine compatibility.
