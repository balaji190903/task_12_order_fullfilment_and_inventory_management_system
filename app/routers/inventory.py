from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles, assert_warehouse_access
from app.models.user import RoleName, User
from app.schemas.inventory import (
    InventoryOut,
    InventoryAdjustRequest,
    InventoryTransferRequest,
)
from app.services import inventory_service
from app.services.idempotency_service import get_cached_response, reserve_idempotency_key, store_response

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

_write_roles = require_roles(RoleName.ADMIN, RoleName.WAREHOUSE_MANAGER)


@router.get("")
def list_inventory(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    warehouse_id: int | None = None,
    product_id: int | None = None,
    low_stock: bool = False,
    out_of_stock: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if warehouse_id is not None:
        assert_warehouse_access(db, current_user, warehouse_id)
    items, total = inventory_service.list_inventory(
        db,
        page=page,
        page_size=page_size,
        warehouse_id=warehouse_id,
        product_id=product_id,
        low_stock=low_stock,
        out_of_stock=out_of_stock,
    )
    return {
        "items": [InventoryOut.model_validate(i) for i in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/low-stock")
def low_stock(
    warehouse_id: int | None = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    if warehouse_id is not None:
        assert_warehouse_access(db, current_user, warehouse_id)
    items = inventory_service.get_low_stock(db, warehouse_id=warehouse_id)
    return [InventoryOut.model_validate(i) for i in items]


@router.get("/{product_id}")
def get_inventory_for_product(
    product_id: int,
    warehouse_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_warehouse_access(db, current_user, warehouse_id)
    inventory = inventory_service.get_inventory(db, product_id=product_id, warehouse_id=warehouse_id)
    return InventoryOut.model_validate(inventory)


@router.post("/adjust", response_model=InventoryOut, dependencies=[Depends(_write_roles)])
def adjust_inventory(
    payload: InventoryAdjustRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    assert_warehouse_access(db, current_user, payload.warehouse_id)

    endpoint = "POST /api/inventory/adjust"
    if payload.idempotency_key:
        cached = get_cached_response(db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key)
        if cached is not None:
            return cached
        reserve_idempotency_key(
            db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key, payload=payload.model_dump()
        )

    inventory = inventory_service.adjust_inventory(
        db,
        product_id=payload.product_id,
        warehouse_id=payload.warehouse_id,
        adjustment=payload.adjustment,
        reason=payload.reason,
        user_id=current_user.id,
    )
    result = InventoryOut.model_validate(inventory).model_dump(mode="json")

    if payload.idempotency_key:
        store_response(
            db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key, status_code=200, body=result
        )

    db.commit()
    return result


@router.post("/transfer", dependencies=[Depends(_write_roles)])
def transfer_inventory(
    payload: InventoryTransferRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    assert_warehouse_access(db, current_user, payload.from_warehouse_id)
    assert_warehouse_access(db, current_user, payload.to_warehouse_id)

    endpoint = "POST /api/inventory/transfer"
    if payload.idempotency_key:
        cached = get_cached_response(db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key)
        if cached is not None:
            return cached
        reserve_idempotency_key(
            db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key, payload=payload.model_dump()
        )

    source, destination = inventory_service.transfer_inventory(
        db,
        product_id=payload.product_id,
        from_warehouse_id=payload.from_warehouse_id,
        to_warehouse_id=payload.to_warehouse_id,
        quantity=payload.quantity,
        user_id=current_user.id,
    )
    result = {
        "source": InventoryOut.model_validate(source).model_dump(mode="json"),
        "destination": InventoryOut.model_validate(destination).model_dump(mode="json"),
    }

    if payload.idempotency_key:
        store_response(
            db, user_id=current_user.id, endpoint=endpoint, key=payload.idempotency_key, status_code=200, body=result
        )

    db.commit()
    return result
