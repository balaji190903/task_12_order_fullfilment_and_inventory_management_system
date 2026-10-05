from datetime import datetime

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str
    description: str | None = None
    category_id: int | None = None
    price: float = Field(ge=0)
    cost_price: float = Field(ge=0, default=0)


class ProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category_id: int | None = None
    price: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)


class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    description: str | None
    category_id: int | None
    price: float
    cost_price: float
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
