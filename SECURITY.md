# Security policy

Do not report exploitable package, updater, signing, or protected-audit issues
in a public issue. Use GitHub private vulnerability reporting for this
repository or contact the Tumoflip maintainer privately.

Package publication is fail-closed. A malformed primary catalog, digest
mismatch, revision collision, source-commit mismatch, unsafe archive member, or
invalid audit ledger must stop publication and client installation. It must not
silently fall back to the legacy repository.

Required repository controls are documented in
[docs/SECURITY.md](docs/SECURITY.md).
