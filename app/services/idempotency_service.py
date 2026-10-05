import hashlib
import json
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions import DuplicateIdempotencyKeyError
from app.models.system import IdempotencyKey


def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def get_cached_response(db: Session, *, user_id: int, endpoint: str, key: str) -> dict | None:
    record = (
        db.query(IdempotencyKey)
        .filter(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.key == key,
        )
        .first()
    )
    if record and record.response_body is not None:
        return record.response_body
    return None


def reserve_idempotency_key(db: Session, *, user_id: int, endpoint: str, key: str, payload: dict) -> None:
    """Inserts a placeholder row up front, inside the same transaction as the
    business operation. The unique constraint on (user_id, endpoint, key)
    means a concurrent duplicate request will fail with IntegrityError, which
    we translate into DuplicateIdempotencyKeyError so the caller can retry
    fetching the (by-then-committed) cached response.
    """
    request_hash = _hash_payload(payload)
    record = IdempotencyKey(
        user_id=user_id,
        endpoint=endpoint,
        key=key,
        request_hash=request_hash,
    )
    db.add(record)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise DuplicateIdempotencyKeyError(
            "This request is already being processed or was already completed; "
            "check GET endpoints for the resulting resource."
        )


def store_response(db: Session, *, user_id: int, endpoint: str, key: str, status_code: int, body: dict) -> None:
    record = (
        db.query(IdempotencyKey)
        .filter(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.key == key,
        )
        .first()
    )
    if record:
        record.response_status = status_code
        record.response_body = body
        db.flush()
