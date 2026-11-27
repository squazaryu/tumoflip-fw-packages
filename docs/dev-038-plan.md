# Dev038 — full snapshot for Dev011-006

The owner authorized the complete firmware/package pair for installation with
the existing TumoCompanion. Firmware011-006 is based on working004, not the
rejected005 runtime changes. Source950d5838 has the same tree as merged
dev972f9075, with successful PR and dev Build/diagnostic/CodeQL checks and an
additional exact-source CodeQL run45967114667.

The release-specific plan pins only the verified published bytes of the reviewed
firmware011-006 package manifest and ZIP. It does not rebuild or independently
update other FAPs, change the current catalog before publication, rewrite
history, change workflow permissions or clear unresolved audit findings.

The approved full inventory remains143 paths and34 cleanup entries. Expected
application metadata includes Weather Editor2.6, RAW Edit1.10 and Field Tools1.1;
the latter deliberately restores the working004 version instead of005's1.2.
API88.23/F7 and the existing application locations are unchanged. No SD
cleanup, data migration, external-board flash or new iOS binary is required.

After exact plan/main validation: independently compose/verify Dev038 against
the published firmware snapshot and immutable Dev037 predecessor, exercise the
released Companion1.12.0 model, publish four immutable package assets, verify
anonymous public bytes, then activate through a separate current/index/lineage
PR. Keep Stable007, prior history and existing audit verdicts unchanged.
Device installation and hardware acceptance remain separate owner tests.
