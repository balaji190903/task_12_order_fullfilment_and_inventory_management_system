from datetime import datetime

from pydantic import BaseModel, Field


class WarehouseCreate(BaseModel):
    name: str
    location: str
    capacity: int = Field(ge=0, default=0)


class WarehouseUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    capacity: int | None = Field(default=None, ge=0)


class WarehouseOut(BaseModel):
    id: int
    name: str
    location: str
    capacity: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
