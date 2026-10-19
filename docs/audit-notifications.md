# Review-only audit notifications

Six scheduled/manual monitoring workflows share `tools/audit_notifications.py`:
Community ABI, source matrix, implementation drift, protected source parity and
history, and ESP installer evidence. Scanners and publication authorization are
unchanged. A successful **monitor** does not mean a verified **audit**.

- A new/changed `needsReview` result updates one canonical bot issue/comment and
  fails the monitor once. An identical repeat keeps that issue open and produces
  a notice/step summary, without editing it or causing another failure email.
- Only top-level report `generatedAt` is excluded from the semantic fingerprint.
  Source commits, findings, policy, evidence timestamps and ABI archive asset
  identities stay significant. A receipt binds the fingerprint to the exact bot
  report body; a missing/edited receipt triggers fresh reconciliation.
- Exceptions, rejected evidence, unavailable sources, malformed status/exit
  combinations and failed GitHub writes stay failures even when repeated.
- Recovery closes an existing tracker once without adding repeated comments.
  Recurrence reopens it. The ESP tracker keeps its human-authored body and uses
  one bot comment; automatic Flash Package authorization stays false until the
  original scanner and hardware evidence actually permit it.
- Only matching bot-authored tracker identities are editable. History lookup
  recognizes the legacy heading-before-marker format instead of creating another
  issue. Historical duplicate issues are preserved, not marked reviewed/accepted.
- Immutable reports continue to be uploaded on every run, even when unchanged.
  Oversized issue bodies are reduced to a bounded excerpt with a workflow link;
  the complete JSON/Markdown remains in the uploaded report, not silently lost.
  Release/publisher gates, reviewed source pins, audit dispositions, package
  contents, firmware and iOS are not changed by notification reconciliation.

After deploying, the first run seeds the receipt on legacy trackers and may fail
once for existing unresolved findings. Subsequent identical runs must not change
the issue body/comment or its update timestamp and must finish with a notice.
New findings or technical failures must still fail. Local `--dry-run` is read-only;
actual issue writes are restricted to the canonical GitHub Actions repository.
