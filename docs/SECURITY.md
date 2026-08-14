# Repository security controls

Configure the public repository before enabling publication:

1. Default workflow permissions: read-only; workflows cannot approve pull
   requests.
2. Protect `main`: pull request required, strict `validate` status required,
   force pushes and deletion disabled, administrators included.
3. Protect `fw-packages-*` and `audit-ledger-*`: creation only by the release
   workflow, update/deletion and force-push forbidden.
4. Create a `production` environment with an owner reviewer. Install a dedicated
   GitHub App only on this repository, grant it `Administration: read` and
   `Contents: write`, and store its ID/private key as
   `PROTECTED_AUDIT_APP_ID` / `PROTECTED_AUDIT_APP_PRIVATE_KEY` environment
   secrets. The publish job requests only those two permissions on an ephemeral
   repository-scoped installation token; the normal workflow token stays read-only.
5. Enable GitHub immutable releases and set the repository variable
   `IMMUTABLE_RELEASES_ENABLED=true` only after the API reports that setting as
   active. The publisher independently checks the authoritative repository API
   before draft mutation and again immediately before publication. Until both
   the operator gate and API proof succeed, audit publication remains blocked.
6. Enable secret scanning, push protection, Dependabot, and private
   vulnerability reporting.
7. Allow only GitHub-owned/verified actions and pin every action to a full
   commit SHA.
8. Do not use a PAT for public read access. If cross-repository dispatch later
   becomes necessary, use a narrowly scoped GitHub App token, never a classic
   PAT.

Untrusted archives are never executed. Both Community Pack ZIPs and FW Packages
target ZIPs are streamed through explicit archive, member-count, per-member,
total-expanded-size, and compression-ratio limits. Verification also rejects
traversal, absolute members, duplicate targets, unexpected members, symlinks,
and digest mismatches before a publish token is available.
