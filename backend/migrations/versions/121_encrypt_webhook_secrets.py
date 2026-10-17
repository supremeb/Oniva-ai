"""Encrypt webhook secrets and backfill trigger owners (fleet/workflows on engine, U20).

Revision ID: 121_encrypt_webhook_secrets
Revises: 120_engine_runs

One transaction, two steps:
1. every plaintext ``auth_config.secret`` / ``auth_config.key`` becomes Fernet ``secret_enc`` / ``key_enc``
   and the plaintext key is removed (a value already encrypted is skipped, so a re-run changes nothing);
2. ``webhook_triggers.created_by`` NULL is backfilled from the workflow owner (workflow trigger) or the
   agent owner (agent trigger). A trigger whose target is gone or has no owner is unresolvable: it is
   logged (WARNING per trigger id) and listed in the returned ``BackfillReport``; it is neither deleted
   nor disabled. At runtime it is refused with 422 ``trigger_unowned``.

The downgrade refuses: the plaintext cannot be restored from a one-way rollout.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import sqlalchemy as sa
from alembic import op

revision = "121_encrypt_webhook_secrets"
down_revision = "120_engine_runs"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_PAIRS = (("secret", "secret_enc"), ("key", "key_enc"))


@dataclass
class UnresolvableTrigger:
    trigger_id: str
    tenant_id: str
    target_kind: str  # "workflow" | "agent" | "none"


@dataclass
class BackfillReport:
    encrypted: int = 0
    backfilled: int = 0
    unresolvable: List[UnresolvableTrigger] = field(default_factory=list)


def backfill(conn) -> BackfillReport:
    """Run both steps on ``conn`` (the caller owns the transaction). Idempotent."""
    from app.services.webhook_secrets import encrypt_secret

    report = BackfillReport()
    rows = conn.execute(sa.text(
        "SELECT id, tenant_id, workflow_id, agent_id, auth_config, created_by FROM webhook_triggers"
    )).mappings().all()
    for row in rows:
        config = row["auth_config"]
        if isinstance(config, str):
            config = json.loads(config)
        config = dict(config or {})
        changed = False
        for plain, enc in _PAIRS:
            if config.get(plain):
                config[enc] = encrypt_secret(str(config[plain]))
                del config[plain]
                changed = True
            elif plain in config:  # empty plaintext: nothing to protect, drop the key
                del config[plain]
                changed = True
        if changed:
            conn.execute(sa.text("UPDATE webhook_triggers SET auth_config = CAST(:c AS jsonb) WHERE id = :i"),
                         {"c": json.dumps(config), "i": row["id"]})
            report.encrypted += 1

        if row["created_by"] is not None:
            continue
        owner: Optional[object] = None
        kind = "none"
        if row["workflow_id"] is not None:
            kind = "workflow"
            owner = conn.execute(sa.text("SELECT user_id FROM workflows WHERE id = :i"),
                                 {"i": row["workflow_id"]}).scalar()
        elif row["agent_id"] is not None:
            kind = "agent"
            owner = conn.execute(sa.text("SELECT user_id FROM custom_agents WHERE id = :i"),
                                 {"i": row["agent_id"]}).scalar()
        if owner is None:
            report.unresolvable.append(UnresolvableTrigger(str(row["id"]), str(row["tenant_id"]), kind))
            logger.warning("webhook trigger %s (tenant %s, target %s) has no resolvable owner; "
                           "it is refused at runtime (trigger_unowned)", row["id"], row["tenant_id"], kind)
            continue
        conn.execute(sa.text("UPDATE webhook_triggers SET created_by = :u WHERE id = :i"),
                     {"u": owner, "i": row["id"]})
        report.backfilled += 1

    logger.info("webhook secrets: %d encrypted, %d owners backfilled, %d unresolvable",
                report.encrypted, report.backfilled, len(report.unresolvable))
    return report


def upgrade() -> None:
    backfill(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("121_encrypt_webhook_secrets cannot be downgraded: plaintext secrets are not restorable")
