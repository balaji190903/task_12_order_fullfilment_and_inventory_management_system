from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import DuplicateSKUError, NotFoundError
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate
from app.services.audit_service import record_audit


def create_product(db: Session, *, data: ProductCreate, user_id: int) -> Product:
    existing = db.query(Product).filter(Product.sku == data.sku).first()
    if existing:
        raise DuplicateSKUError(f"SKU '{data.sku}' already exists")

    product = Product(**data.model_dump())
    db.add(product)
    db.flush()
    record_audit(db, user_id=user_id, action="PRODUCT_CREATED", entity="product", entity_id=product.id)
    db.commit()
    db.refresh(product)
    return product


def list_products(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    category_id: int | None = None,
    is_active: bool | None = None,
    search: str | None = None,
) -> tuple[list[Product], int]:
    stmt = select(Product)
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if is_active is not None:
        stmt = stmt.where(Product.is_active == is_active)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Product.name.ilike(like) | Product.sku.ilike(like))

    total = len(db.execute(stmt).scalars().all())
    stmt = stmt.order_by(Product.id).offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total


def get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if not product:
        raise NotFoundError(f"Product {product_id} not found")
    return product


def update_product(db: Session, *, product_id: int, data: ProductUpdate, user_id: int) -> Product:
    product = get_product(db, product_id)
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(product, field, value)
    db.flush()
    record_audit(db, user_id=user_id, action="PRODUCT_UPDATED", entity="product", entity_id=product.id, meta=updates)
    db.commit()
    db.refresh(product)
    return product


def set_product_active(db: Session, *, product_id: int, is_active: bool, user_id: int) -> Product:
    product = get_product(db, product_id)
    product.is_active = is_active
    db.flush()
    action = "PRODUCT_ACTIVATED" if is_active else "PRODUCT_DEACTIVATED"
    record_audit(db, user_id=user_id, action=action, entity="product", entity_id=product.id)
    db.commit()
    db.refresh(product)
    return product
