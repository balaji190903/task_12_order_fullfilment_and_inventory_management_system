from datetime import datetime, timezone

from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.exceptions import ConflictError, UnauthorizedError, ValidationErrorApp
from app.models.user import User, RefreshToken, RoleName
from app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.audit_service import record_audit


def register_user(db: Session, *, email: str, password: str, full_name: str, role: RoleName) -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ConflictError("A user with this email already exists", code="DUPLICATE_EMAIL")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush() 
    record_audit(db, user_id=user.id, action="USER_REGISTERED", entity="user", entity_id=user.id)
    db.commit()
    db.refresh(user)
    return user


def _issue_tokens(db: Session, user: User) -> tuple[str, str]:
    access_token = create_access_token(user.id, user.role.value)
    refresh_token, jti, expires_at = create_refresh_token(user.id)
    db.add(RefreshToken(jti=jti, user_id=user.id, expires_at=expires_at))
    db.flush()
    return access_token, refresh_token


def login(db: Session, *, email: str, password: str, ip_address: str | None) -> tuple[str, str]:
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        if user:
            record_audit(db, user_id=user.id, action="LOGIN_FAILED", entity="user", entity_id=user.id, ip_address=ip_address)
            db.commit()
        raise UnauthorizedError("Invalid email or password")

    if not user.is_active:
        record_audit(db, user_id=user.id, action="LOGIN_FAILED", entity="user", entity_id=user.id, ip_address=ip_address, meta={"reason": "inactive"})
        db.commit()
        raise UnauthorizedError("User account is inactive")

    access_token, refresh_token = _issue_tokens(db, user)
    record_audit(db, user_id=user.id, action="LOGIN", entity="user", entity_id=user.id, ip_address=ip_address)
    db.commit()
    return access_token, refresh_token


def refresh_access_token(db: Session, *, refresh_token: str) -> tuple[str, str]:
    """Rotates the refresh token: the old one is revoked and a brand-new one is
    issued, alongside a new access token. If a revoked/unknown jti is
    presented, the request is rejected - this both blocks token replay and
    lets us detect refresh-token theft (a revoked token being reused).
    """
    try:
        payload = decode_token(refresh_token)
    except PyJWTError:
        raise UnauthorizedError("Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise UnauthorizedError("Invalid token type")

    jti = payload.get("jti")
    record = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if not record or record.revoked:
        raise UnauthorizedError("Refresh token has been revoked or is unknown")
    if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise UnauthorizedError("Refresh token has expired")

    user = db.get(User, record.user_id)
    if not user or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    # Rotate: revoke old, issue new
    record.revoked = True
    db.flush()
    access_token, new_refresh_token = _issue_tokens(db, user)
    db.commit()
    return access_token, new_refresh_token


def logout(db: Session, *, refresh_token: str) -> None:
    try:
        payload = decode_token(refresh_token)
    except PyJWTError:
        return  # already invalid; nothing to revoke
    jti = payload.get("jti")
    record = db.query(RefreshToken).filter(RefreshToken.jti == jti).first()
    if record:
        record.revoked = True
        db.commit()


def change_password(db: Session, *, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.hashed_password):
        raise ValidationErrorApp("Current password is incorrect")
    user.hashed_password = hash_password(new_password)
    # Revoke all outstanding refresh tokens on password change
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False)).update(
        {"revoked": True}
    )
    record_audit(db, user_id=user.id, action="PASSWORD_CHANGED", entity="user", entity_id=user.id)
    db.commit()
