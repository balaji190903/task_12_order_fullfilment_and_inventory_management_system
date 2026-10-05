from fastapi import Depends, Header
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.database import get_db
from app.exceptions import UnauthorizedError, ForbiddenError, WarehouseAccessDeniedError
from app.models.user import User, RoleName, WarehouseUser
from app.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise UnauthorizedError("Missing authentication token")
    try:
        payload = decode_token(token)
    except PyJWTError:
        raise UnauthorizedError("Invalid or expired token")

    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")

    user_id = payload.get("sub")
    user = db.get(User, int(user_id)) if user_id else None
    if not user:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise UnauthorizedError("User account is inactive")
    return user


def require_roles(*allowed_roles: RoleName):
    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise ForbiddenError(
                f"Role '{current_user.role.value}' is not permitted to perform this action"
            )
        return current_user

    return _checker


def get_client_ip(x_forwarded_for: str | None = Header(default=None)) -> str | None:
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return None


def assert_warehouse_access(db: Session, user: User, warehouse_id: int) -> None:
    """Admins may access any warehouse. Warehouse managers / fulfillment agents
    are restricted to warehouses they are explicitly linked to via WarehouseUser.
    """
    if user.role == RoleName.ADMIN:
        return
    link = (
        db.query(WarehouseUser)
        .filter(WarehouseUser.warehouse_id == warehouse_id, WarehouseUser.user_id == user.id)
        .first()
    )
    if not link:
        raise WarehouseAccessDeniedError(
            f"User does not have access to warehouse {warehouse_id}"
        )
