from datetime import datetime

from pydantic import BaseModel, Field

from app.models.return_ import ReturnStatus


class ReturnItemCreate(BaseModel):
    order_item_id: int
    quantity: int = Field(gt=0)


class ReturnCreate(BaseModel):
    order_id: int
    reason: str | None = None
    items: list[ReturnItemCreate] = Field(min_length=1)


class ReturnStatusUpdate(BaseModel):
    status: ReturnStatus


class ReturnItemOut(BaseModel):
    id: int
    order_item_id: int
    quantity: int

    class Config:
        from_attributes = True


class ReturnOut(BaseModel):
    id: int
    order_id: int
    customer_id: int
    status: ReturnStatus
    reason: str | None
    created_at: datetime
    updated_at: datetime
    items: list[ReturnItemOut] = []

    class Config:
        from_attributes = True


class NotificationOut(BaseModel):
    id: int
    event_type: str
    title: str
    message: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AdminDashboardOut(BaseModel):
    total_orders: int
    pending_orders: int
    processing_orders: int
    completed_orders: int
    cancelled_orders: int
    total_revenue: float
    low_stock_products: int
    out_of_stock_products: int


class WarehouseManagerDashboardOut(BaseModel):
    warehouse_id: int
    warehouse_inventory_items: int
    pending_fulfillment: int
    processing_orders: int
    low_stock_items: int
    todays_shipments: int


class CustomerDashboardOut(BaseModel):
    total_orders: int
    active_orders: int
    completed_orders: int
    cancelled_orders: int
    total_spending: float
