from datetime import datetime

from pydantic import BaseModel, Field

from app.models.inventory import InventoryTransactionType


class InventoryOut(BaseModel):
    id: int
    product_id: int
    warehouse_id: int
    available_quantity: int
    reserved_quantity: int
    damaged_quantity: int
    reorder_level: int
    updated_at: datetime

    class Config:
        from_attributes = True


class InventoryAdjustRequest(BaseModel):
    product_id: int
    warehouse_id: int
    adjustment: int = Field(description="Signed delta applied to available_quantity")
    reason: str
    idempotency_key: str | None = None


class InventoryTransferRequest(BaseModel):
    product_id: int
    from_warehouse_id: int
    to_warehouse_id: int
    quantity: int = Field(gt=0)
    idempotency_key: str | None = None


class InventoryTransactionOut(BaseModel):
    id: int
    product_id: int
    warehouse_id: int
    transaction_type: InventoryTransactionType
    quantity: int
    previous_quantity: int
    new_quantity: int
    reference_entity: str | None
    reference_id: int | None
    reason: str | None
    user_id: int | None
    created_at: datetime

    class Config:
        from_attributes = True
