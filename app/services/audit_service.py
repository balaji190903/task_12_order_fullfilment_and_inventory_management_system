from sqlalchemy.orm import Session

from app.models.system import AuditLog


def record_audit(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    entity: str,
    entity_id: int | None,
    ip_address: str | None = None,
    meta: dict | None = None,
) -> AuditLog:
    """Appends an immutable audit log row. Caller controls the commit boundary
    so this can participate in the same transaction as the business operation
    it is auditing (audit log write and business change succeed/fail together).
    """
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        ip_address=ip_address,
        meta=meta,
    )
    db.add(log)
    db.flush()
    return log
