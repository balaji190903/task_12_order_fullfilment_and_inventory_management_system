from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.inventory import Inventory
from app.models.order import Order, OrderStatus
from app.models.user import User


def admin_dashboard(db: Session) -> dict:
    total_orders = db.query(func.count(Order.id)).scalar() or 0
    pending = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.PENDING).scalar() or 0
    processing = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.PROCESSING).scalar() or 0
    completed = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.DELIVERED).scalar() or 0
    cancelled = db.query(func.count(Order.id)).filter(Order.status == OrderStatus.CANCELLED).scalar() or 0
    revenue = (
        db.query(func.coalesce(func.sum(Order.total_amount), 0))
        .filter(Order.status.in_([OrderStatus.DELIVERED, OrderStatus.SHIPPED]))
        .scalar()
        or 0
    )
    low_stock = db.query(func.count(Inventory.id)).filter(Inventory.available_quantity <= Inventory.reorder_level).scalar() or 0
    out_of_stock = db.query(func.count(Inventory.id)).filter(Inventory.available_quantity == 0).scalar() or 0

    return {
        "total_orders": total_orders,
        "pending_orders": pending,
        "processing_orders": processing,
        "completed_orders": completed,
        "cancelled_orders": cancelled,
        "total_revenue": float(revenue),
        "low_stock_products": low_stock,
        "out_of_stock_products": out_of_stock,
    }


def warehouse_manager_dashboard(db: Session, *, warehouse_id: int) -> dict:
    inventory_items = db.query(func.count(Inventory.id)).filter(Inventory.warehouse_id == warehouse_id).scalar() or 0
    pending_fulfillment = (
        db.query(func.count(Order.id))
        .filter(Order.warehouse_id == warehouse_id, Order.status.in_([OrderStatus.CONFIRMED, OrderStatus.PENDING]))
        .scalar()
        or 0
    )
    processing = (
        db.query(func.count(Order.id))
        .filter(Order.warehouse_id == warehouse_id, Order.status == OrderStatus.PROCESSING)
        .scalar()
        or 0
    )
    low_stock = (
        db.query(func.count(Inventory.id))
        .filter(Inventory.warehouse_id == warehouse_id, Inventory.available_quantity <= Inventory.reorder_level)
        .scalar()
        or 0
    )
    today = datetime.now(timezone.utc).date()
    todays_shipments = (
        db.query(func.count(Order.id))
        .filter(
            Order.warehouse_id == warehouse_id,
            Order.status == OrderStatus.SHIPPED,
            func.date(Order.updated_at) == today,
        )
        .scalar()
        or 0
    )

    return {
        "warehouse_id": warehouse_id,
        "warehouse_inventory_items": inventory_items,
        "pending_fulfillment": pending_fulfillment,
        "processing_orders": processing,
        "low_stock_items": low_stock,
        "todays_shipments": todays_shipments,
    }


def customer_dashboard(db: Session, *, customer_id: int) -> dict:
    total_orders = db.query(func.count(Order.id)).filter(Order.customer_id == customer_id).scalar() or 0
    active = (
        db.query(func.count(Order.id))
        .filter(
            Order.customer_id == customer_id,
            Order.status.in_(
                [OrderStatus.PENDING, OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.PACKED, OrderStatus.SHIPPED]
            ),
        )
        .scalar()
        or 0
    )
    completed = (
        db.query(func.count(Order.id))
        .filter(Order.customer_id == customer_id, Order.status == OrderStatus.DELIVERED)
        .scalar()
        or 0
    )
    cancelled = (
        db.query(func.count(Order.id))
        .filter(Order.customer_id == customer_id, Order.status == OrderStatus.CANCELLED)
        .scalar()
        or 0
    )
    spending = (
        db.query(func.coalesce(func.sum(Order.total_amount), 0))
        .filter(
            Order.customer_id == customer_id,
            Order.status.in_([OrderStatus.DELIVERED, OrderStatus.SHIPPED]),
        )
        .scalar()
        or 0
    )

    return {
        "total_orders": total_orders,
        "active_orders": active,
        "completed_orders": completed,
        "cancelled_orders": cancelled,
        "total_spending": float(spending),
    }
