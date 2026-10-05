from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.exceptions import (
    InvalidOrderStatusError,
    NotFoundError,
    ProductInactiveError,
    ForbiddenError,
)
from app.models.order import (
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    OrderAssignment,
    ORDER_TRANSITIONS,
)
from app.models.product import Product
from app.models.user import User, RoleName
from app.schemas.order import OrderCreate
from app.services import inventory_service
from app.services.audit_service import record_audit
from app.services.notification_service import create_notification

settings = get_settings()


def _record_status_change(
    db: Session, *, order: Order, to_status: OrderStatus, user_id: int | None, note: str | None = None
) -> None:
    db.add(
        OrderStatusHistory(
            order_id=order.id,
            from_status=order.status,
            to_status=to_status,
            changed_by_user_id=user_id,
            note=note,
        )
    )
    order.status = to_status
    db.flush()


def create_order(db: Session, *, customer: User, data: OrderCreate) -> Order:
    """Creates the order, its items (with price snapshotted from the current
    product price), and reserves inventory for every line item - all inside
    one transaction. If any item can't be reserved (product inactive,
    insufficient stock), the whole order creation is rolled back so no
    partial reservation / partial order is left behind.
    """
    order = Order(customer_id=customer.id, warehouse_id=data.warehouse_id, total_amount=0)
    db.add(order)
    db.flush()  # assign order.id

    total_amount = 0.0
    for item in data.items:
        product = db.get(Product, item.product_id)
        if not product:
            raise NotFoundError(f"Product {item.product_id} not found")
        if not product.is_active:
            raise ProductInactiveError(f"Product '{product.sku}' is inactive and cannot be ordered")

        # Reserve inventory first - raises InsufficientInventoryError if unavailable.
        # This call takes a row lock on the (product, warehouse) inventory row,
        # which is what serializes concurrent orders for the same stock.
        inventory_service.reserve_inventory(
            db,
            product_id=product.id,
            warehouse_id=data.warehouse_id,
            quantity=item.quantity,
            reference_entity="order",
            reference_id=order.id,
            user_id=customer.id,
        )

        unit_price = float(product.price)  # snapshot - future price changes won't affect this order
        line_total = (unit_price * item.quantity) - item.discount + item.tax
        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=item.quantity,
            unit_price=unit_price,
            discount=item.discount,
            tax=item.tax,
            total=line_total,
        )
        db.add(order_item)
        total_amount += line_total

    order.total_amount = total_amount
    order.expected_delivery_date = data.expected_delivery_date
    db.flush()

    _record_status_change(db, order=order, to_status=OrderStatus.PENDING, user_id=customer.id, note="Order created")
    record_audit(db, user_id=customer.id, action="ORDER_CREATED", entity="order", entity_id=order.id)
    create_notification(
        db,
        user_id=customer.id,
        event_type="ORDER_CREATED",
        title="Order placed",
        message=f"Your order {order.order_number} has been created.",
    )
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> Order:
    stmt = (
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items))
    )
    order = db.execute(stmt).scalar_one_or_none()
    if not order:
        raise NotFoundError(f"Order {order_id} not found")
    return order


def assert_can_view_order(order: Order, user: User) -> None:
    if user.role == RoleName.CUSTOMER and order.customer_id != user.id:
        raise ForbiddenError("Customers may only view their own orders")


def list_orders(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    customer_id: int | None = None,
    warehouse_id: int | None = None,
    status: OrderStatus | None = None,
    payment_status=None,
) -> tuple[list[Order], int]:
    stmt = select(Order).options(selectinload(Order.items))
    if customer_id is not None:
        stmt = stmt.where(Order.customer_id == customer_id)
    if warehouse_id is not None:
        stmt = stmt.where(Order.warehouse_id == warehouse_id)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    if payment_status is not None:
        stmt = stmt.where(Order.payment_status == payment_status)

    total = len(db.execute(stmt).scalars().unique().all())
    stmt = stmt.order_by(Order.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().unique().all())
    return items, total


def transition_order_status(
    db: Session, *, order: Order, to_status: OrderStatus, user_id: int | None, note: str | None = None
) -> Order:
    allowed = ORDER_TRANSITIONS.get(order.status, set())
    if to_status not in allowed:
        raise InvalidOrderStatusError(
            f"Cannot transition order from '{order.status.value}' to '{to_status.value}'"
        )

    if to_status == OrderStatus.CANCELLED:
        _cancel_order_internal(db, order=order, user_id=user_id, note=note)
        db.commit()
        db.refresh(order)
        return order

    if to_status == OrderStatus.SHIPPED:
        # Convert reservations into permanent sales at ship time
        for item in order.items:
            inventory_service.release_inventory(
                db,
                product_id=item.product_id,
                warehouse_id=order.warehouse_id,
                quantity=item.quantity,
                reference_entity="order",
                reference_id=order.id,
                user_id=user_id,
                convert_to_sale=True,
            )

    _record_status_change(db, order=order, to_status=to_status, user_id=user_id, note=note)
    record_audit(db, user_id=user_id, action="ORDER_STATUS_CHANGED", entity="order", entity_id=order.id, meta={"to": to_status.value})

    event_map = {
        OrderStatus.CONFIRMED: ("ORDER_CONFIRMED", "Your order has been confirmed."),
        OrderStatus.SHIPPED: ("ORDER_SHIPPED", "Your order has shipped."),
        OrderStatus.DELIVERED: ("ORDER_DELIVERED", "Your order has been delivered."),
    }
    if to_status in event_map:
        event_type, message = event_map[to_status]
        create_notification(
            db, user_id=order.customer_id, event_type=event_type, title=event_type.replace("_", " ").title(),
            message=f"{message} ({order.order_number})",
        )

    db.commit()
    db.refresh(order)
    return order


def _cancel_order_internal(db: Session, *, order: Order, user_id: int | None, note: str | None) -> None:
    if order.status not in [OrderStatus(s) for s in settings.CANCELLABLE_STATUSES]:
        raise InvalidOrderStatusError(
            f"Order in status '{order.status.value}' can no longer be cancelled"
        )

    for item in order.items:
        # Release whatever is still reserved for this line (idempotent-ish:
        # if quantity already released this will raise, which is desired -
        # it signals a bug rather than silently double-releasing).
        inventory_service.release_inventory(
            db,
            product_id=item.product_id,
            warehouse_id=order.warehouse_id,
            quantity=item.quantity,
            reference_entity="order",
            reference_id=order.id,
            user_id=user_id,
        )

    _record_status_change(db, order=order, to_status=OrderStatus.CANCELLED, user_id=user_id, note=note)
    record_audit(db, user_id=user_id, action="ORDER_CANCELLED", entity="order", entity_id=order.id)
    create_notification(
        db,
        user_id=order.customer_id,
        event_type="ORDER_CANCELLED",
        title="Order cancelled",
        message=f"Your order {order.order_number} has been cancelled and any reserved stock released.",
    )


def cancel_order(db: Session, *, order: Order, user: User, note: str | None = None) -> Order:
    if user.role == RoleName.CUSTOMER and order.customer_id != user.id:
        raise ForbiddenError("Customers may only cancel their own orders")
    _cancel_order_internal(db, order=order, user_id=user.id, note=note)
    db.commit()
    db.refresh(order)
    return order


def assign_order(db: Session, *, order: Order, agent: User, assigned_by: User) -> OrderAssignment:
    if agent.role != RoleName.FULFILLMENT_AGENT:
        raise ForbiddenError("Orders can only be assigned to fulfillment agents")
    if not agent.is_active:
        raise ForbiddenError("Cannot assign an order to an inactive agent")

    # Deactivate any existing active assignment (reassignment)
    for existing in order.assignments:
        if existing.is_active:
            existing.is_active = False

    assignment = OrderAssignment(order_id=order.id, agent_id=agent.id, assigned_by_user_id=assigned_by.id)
    db.add(assignment)
    db.flush()

    record_audit(
        db,
        user_id=assigned_by.id,
        action="ORDER_ASSIGNED",
        entity="order",
        entity_id=order.id,
        meta={"agent_id": agent.id},
    )
    create_notification(
        db,
        user_id=agent.id,
        event_type="ORDER_ASSIGNED",
        title="New order assigned",
        message=f"Order {order.order_number} has been assigned to you.",
    )
    db.commit()
    db.refresh(assignment)
    return assignment
