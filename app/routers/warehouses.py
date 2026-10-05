from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles, assert_warehouse_access
from app.models.user import RoleName, User
from app.schemas.warehouse import WarehouseCreate, WarehouseUpdate, WarehouseOut
from app.services import warehouse_service

router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])

_manage_roles = require_roles(RoleName.ADMIN)


@router.post("", response_model=WarehouseOut, status_code=201, dependencies=[Depends(_manage_roles)])
def create_warehouse(payload: WarehouseCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return warehouse_service.create_warehouse(db, data=payload, user_id=current_user.id)


@router.get("")
def list_warehouses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = warehouse_service.list_warehouses(db, page=page, page_size=page_size, is_active=is_active)
    return {
        "items": [WarehouseOut.model_validate(w) for w in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/{warehouse_id}", response_model=WarehouseOut)
def get_warehouse(warehouse_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_warehouse_access(db, current_user, warehouse_id)
    return warehouse_service.get_warehouse(db, warehouse_id)


@router.put("/{warehouse_id}", response_model=WarehouseOut, dependencies=[Depends(_manage_roles)])
def update_warehouse(
    warehouse_id: int, payload: WarehouseUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return warehouse_service.update_warehouse(db, warehouse_id=warehouse_id, data=payload, user_id=current_user.id)


@router.patch("/{warehouse_id}/activate", response_model=WarehouseOut, dependencies=[Depends(_manage_roles)])
def activate_warehouse(warehouse_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return warehouse_service.set_warehouse_active(db, warehouse_id=warehouse_id, is_active=True, user_id=current_user.id)


@router.patch("/{warehouse_id}/deactivate", response_model=WarehouseOut, dependencies=[Depends(_manage_roles)])
def deactivate_warehouse(warehouse_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return warehouse_service.set_warehouse_active(db, warehouse_id=warehouse_id, is_active=False, user_id=current_user.id)
