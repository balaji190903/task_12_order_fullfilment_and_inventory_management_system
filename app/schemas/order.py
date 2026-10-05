from datetime import datetime

from pydantic import BaseModel, Field

from app.models.order import OrderStatus, PaymentStatus


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    discount: float = Field(ge=0, default=0)
    tax: float = Field(ge=0, default=0)


class OrderCreate(BaseModel):
    warehouse_id: int
    items: list[OrderItemCreate] = Field(min_length=1)
    expected_delivery_date: datetime | None = None
    idempotency_key: str | None = None


class OrderItemOut(BaseModel):
    id: int
    product_id: int
    quantity: int
    unit_price: float
    discount: float
    tax: float
    total: float

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    order_number: str
    customer_id: int
    warehouse_id: int
    total_amount: float
    status: OrderStatus
    payment_status: PaymentStatus
    expected_delivery_date: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut] = []

    class Config:
        from_attributes = True


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    note: str | None = None


class OrderAssignRequest(BaseModel):
    agent_id: int


class PaginatedOrders(BaseModel):
    items: list[OrderOut]
    page: int
    page_size: int
    total: int
