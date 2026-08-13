# Repository security controls

Configure the public repository before enabling publication:

1. Default workflow permissions: read-only; workflows cannot approve pull
   requests.
2. Protect `main`: pull request required, strict `validate` status required,
   force pushes and deletion disabled, administrators included.
3. Protect `fw-packages-*` and `audit-ledger-*`: creation only by the release
   workflow, update/deletion and force-push forbidden.
4. Create a `production` environment with an owner reviewer. Only the publish
   job receives `contents: write`.
5. Enable secret scanning, push protection, Dependabot, and private
   vulnerability reporting.
6. Allow only GitHub-owned/verified actions and pin every action to a full
   commit SHA.
7. Do not use a PAT for public read access. If cross-repository dispatch later
   becomes necessary, use a narrowly scoped GitHub App token, never a classic
   PAT.

Untrusted archives are never executed. Verification rejects traversal,
absolute ZIP members, duplicate targets, unexpected members, symlinks, digest
mismatches, and oversized files before a publish token is available.
