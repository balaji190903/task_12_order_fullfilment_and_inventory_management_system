from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.exceptions import InvalidReturnStatusError, NotFoundError, ForbiddenError
from app.models.order import Order, OrderItem
from app.models.return_ import Return, ReturnItem, ReturnStatus, RETURN_TRANSITIONS
from app.models.user import User, RoleName
from app.schemas.misc import ReturnCreate
from app.services import inventory_service
from app.services.audit_service import record_audit
from app.services.notification_service import create_notification


def create_return(db: Session, *, customer: User, data: ReturnCreate) -> Return:
    order = db.get(Order, data.order_id)
    if not order:
        raise NotFoundError(f"Order {data.order_id} not found")
    if order.customer_id != customer.id and customer.role == RoleName.CUSTOMER:
        raise ForbiddenError("Customers may only return their own orders")

    return_ = Return(order_id=order.id, customer_id=order.customer_id, reason=data.reason)
    db.add(return_)
    db.flush()

    for item in data.items:
        order_item = db.get(OrderItem, item.order_item_id)
        if not order_item or order_item.order_id != order.id:
            raise NotFoundError(f"Order item {item.order_item_id} not found on this order")
        if item.quantity > order_item.quantity:
            raise InvalidReturnStatusError("Return quantity exceeds ordered quantity")
        db.add(ReturnItem(return_id=return_.id, order_item_id=order_item.id, quantity=item.quantity))

    db.flush()
    record_audit(db, user_id=customer.id, action="RETURN_CREATED", entity="return", entity_id=return_.id)
    create_notification(
        db,
        user_id=order.customer_id,
        event_type="RETURN_REQUESTED",
        title="Return requested",
        message=f"Your return request for order {order.order_number} has been submitted.",
    )
    db.commit()
    db.refresh(return_)
    return return_


def get_return(db: Session, return_id: int) -> Return:
    stmt = select(Return).where(Return.id == return_id).options(selectinload(Return.items))
    return_ = db.execute(stmt).scalar_one_or_none()
    if not return_:
        raise NotFoundError(f"Return {return_id} not found")
    return return_


def transition_return_status(db: Session, *, return_: Return, to_status: ReturnStatus, user: User) -> Return:
    allowed = RETURN_TRANSITIONS.get(return_.status, set())
    if to_status not in allowed:
        raise InvalidReturnStatusError(
            f"Cannot transition return from '{return_.status.value}' to '{to_status.value}'"
        )

    if to_status == ReturnStatus.RECEIVED:
        # Returned products go back into available inventory for the order's warehouse
        order = db.get(Order, return_.order_id)
        for return_item in return_.items:
            order_item = db.get(OrderItem, return_item.order_item_id)
            inventory_service.adjust_inventory(
                db,
                product_id=order_item.product_id,
                warehouse_id=order.warehouse_id,
                adjustment=return_item.quantity,
                reason="Customer return received",
                user_id=user.id,
            )

    return_.status = to_status
    db.flush()

    action_map = {
        ReturnStatus.APPROVED: "RETURN_APPROVED",
        ReturnStatus.REJECTED: "RETURN_REJECTED",
        ReturnStatus.RECEIVED: "RETURN_RECEIVED",
        ReturnStatus.REFUNDED: "RETURN_REFUNDED",
    }
    record_audit(db, user_id=user.id, action=action_map[to_status], entity="return", entity_id=return_.id)

    if to_status == ReturnStatus.APPROVED:
        create_notification(
            db,
            user_id=return_.customer_id,
            event_type="RETURN_APPROVED",
            title="Return approved",
            message="Your return request has been approved.",
        )

    db.commit()
    db.refresh(return_)
    return return_
