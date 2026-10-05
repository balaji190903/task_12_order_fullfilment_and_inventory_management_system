from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import NotFoundError
from app.models.warehouse import Warehouse
from app.schemas.warehouse import WarehouseCreate, WarehouseUpdate
from app.services.audit_service import record_audit


def create_warehouse(db: Session, *, data: WarehouseCreate, user_id: int) -> Warehouse:
    warehouse = Warehouse(**data.model_dump())
    db.add(warehouse)
    db.flush()
    record_audit(db, user_id=user_id, action="WAREHOUSE_CREATED", entity="warehouse", entity_id=warehouse.id)
    db.commit()
    db.refresh(warehouse)
    return warehouse


def list_warehouses(db: Session, *, page: int = 1, page_size: int = 20, is_active: bool | None = None):
    stmt = select(Warehouse)
    if is_active is not None:
        stmt = stmt.where(Warehouse.is_active == is_active)
    total = len(db.execute(stmt).scalars().all())
    stmt = stmt.order_by(Warehouse.id).offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total


def get_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if not warehouse:
        raise NotFoundError(f"Warehouse {warehouse_id} not found")
    return warehouse


def update_warehouse(db: Session, *, warehouse_id: int, data: WarehouseUpdate, user_id: int) -> Warehouse:
    warehouse = get_warehouse(db, warehouse_id)
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(warehouse, field, value)
    db.flush()
    record_audit(db, user_id=user_id, action="WAREHOUSE_UPDATED", entity="warehouse", entity_id=warehouse.id, meta=updates)
    db.commit()
    db.refresh(warehouse)
    return warehouse


def set_warehouse_active(db: Session, *, warehouse_id: int, is_active: bool, user_id: int) -> Warehouse:
    warehouse = get_warehouse(db, warehouse_id)
    warehouse.is_active = is_active
    db.flush()
    action = "WAREHOUSE_ACTIVATED" if is_active else "WAREHOUSE_DEACTIVATED"
    record_audit(db, user_id=user_id, action=action, entity="warehouse", entity_id=warehouse.id)
    db.commit()
    db.refresh(warehouse)
    return warehouse
