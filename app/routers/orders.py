from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles, assert_warehouse_access
from app.exceptions import ForbiddenError, NotFoundError
from app.models.user import RoleName, User
from app.models.order import OrderStatus, PaymentStatus
from app.schemas.order import OrderCreate, OrderOut, OrderStatusUpdate, OrderAssignRequest
from app.services import order_service
from app.services.idempotency_service import get_cached_response, reserve_idempotency_key, store_response

router = APIRouter(prefix="/api/orders", tags=["orders"])

_staff_roles = require_roles(RoleName.ADMIN, RoleName.WAREHOUSE_MANAGER, RoleName.FULFILLMENT_AGENT)
_assign_roles = require_roles(RoleName.ADMIN, RoleName.WAREHOUSE_MANAGER)


@router.post("", response_model=OrderOut, status_code=201, dependencies=[Depends(require_roles(RoleName.CUSTOMER))])
def create_order(payload: OrderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    endpoint = "POST /api/orders"
    if payload.idempotency_key:
        cached = get_cached_response(db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key)
        if cached is not None:
            return cached
        reserve_idempotency_key(
            db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key,
            payload=payload.model_dump(mode="json"),
        )

    order = order_service.create_order(db, customer=current_user, data=payload)
    result = OrderOut.model_validate(order).model_dump(mode="json")

    if payload.idempotency_key:
        store_response(
            db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key, status_code=201, body=result
        )
        db.commit()

    return result


@router.get("")
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: OrderStatus | None = None,
    payment_status: PaymentStatus | None = None,
    warehouse_id: int | None = None,
    customer_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role == RoleName.CUSTOMER:
        customer_id = current_user.id
    elif warehouse_id is not None and current_user.role != RoleName.ADMIN:
        assert_warehouse_access(db, current_user, warehouse_id)

    items, total = order_service.list_orders(
        db,
        page=page,
        page_size=page_size,
        customer_id=customer_id,
        warehouse_id=warehouse_id,
        status=status,
        payment_status=payment_status,
    )
    return {
        "items": [OrderOut.model_validate(o) for o in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    order = order_service.get_order(db, order_id)
    order_service.assert_can_view_order(order, current_user)
    if current_user.role in (RoleName.WAREHOUSE_MANAGER, RoleName.FULFILLMENT_AGENT):
        assert_warehouse_access(db, current_user, order.warehouse_id)
    return order


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    order = order_service.get_order(db, order_id)
    return order_service.cancel_order(db, order=order, user=current_user)


@router.patch("/{order_id}/status", response_model=OrderOut, dependencies=[Depends(_staff_roles)])
def update_order_status(
    order_id: int, payload: OrderStatusUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    order = order_service.get_order(db, order_id)
    if current_user.role != RoleName.ADMIN:
        assert_warehouse_access(db, current_user, order.warehouse_id)
    return order_service.transition_order_status(
        db, order=order, to_status=payload.status, user_id=current_user.id, note=payload.note
    )


@router.post("/{order_id}/assign", dependencies=[Depends(_assign_roles)])
def assign_order(
    order_id: int, payload: OrderAssignRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    order = order_service.get_order(db, order_id)
    if current_user.role != RoleName.ADMIN:
        assert_warehouse_access(db, current_user, order.warehouse_id)

    from app.models.user import User as UserModel

    agent = db.get(UserModel, payload.agent_id)
    if not agent:
        raise NotFoundError(f"Agent {payload.agent_id} not found")
    assignment = order_service.assign_order(db, order=order, agent=agent, assigned_by=current_user)
    return {
        "id": assignment.id,
        "order_id": assignment.order_id,
        "agent_id": assignment.agent_id,
        "is_active": assignment.is_active,
    }
