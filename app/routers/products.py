from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models.user import RoleName, User
from app.schemas.product import ProductCreate, ProductUpdate, ProductOut
from app.services import product_service

router = APIRouter(prefix="/api/products", tags=["products"])


@router.post("", response_model=ProductOut, status_code=201, dependencies=[Depends(require_roles(RoleName.ADMIN))])
def create_product(payload: ProductCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return product_service.create_product(db, data=payload, user_id=current_user.id)


@router.get("")
def list_products(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category_id: int | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items, total = product_service.list_products(
        db, page=page, page_size=page_size, category_id=category_id, is_active=is_active, search=search
    )
    return {
        "items": [ProductOut.model_validate(p) for p in items],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return product_service.get_product(db, product_id)


@router.put("/{product_id}", response_model=ProductOut, dependencies=[Depends(require_roles(RoleName.ADMIN))])
def update_product(
    product_id: int, payload: ProductUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return product_service.update_product(db, product_id=product_id, data=payload, user_id=current_user.id)


@router.patch(
    "/{product_id}/activate", response_model=ProductOut, dependencies=[Depends(require_roles(RoleName.ADMIN))]
)
def activate_product(product_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return product_service.set_product_active(db, product_id=product_id, is_active=True, user_id=current_user.id)


@router.patch(
    "/{product_id}/deactivate", response_model=ProductOut, dependencies=[Depends(require_roles(RoleName.ADMIN))]
)
def deactivate_product(product_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return product_service.set_product_active(db, product_id=product_id, is_active=False, user_id=current_user.id)
