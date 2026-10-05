import enum
from datetime import datetime

from sqlalchemy import (
    Integer,
    DateTime,
    ForeignKey,
    Enum,
    String,
    JSON,
    func,
    CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InventoryTransactionType(str, enum.Enum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RESERVATION = "RESERVATION"
    RELEASE = "RELEASE"
    ADJUSTMENT = "ADJUSTMENT"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    RETURN = "RETURN"
    DAMAGE = "DAMAGE"


class Inventory(Base):
    """One row per (product, warehouse). This row is the row-level lock target
    for reservation/release/adjust/transfer operations (SELECT ... FOR UPDATE).
    """

    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_id", name="uq_inventory_product_warehouse"),
        CheckConstraint("available_quantity >= 0", name="ck_inventory_available_non_negative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_non_negative"),
        CheckConstraint("damaged_quantity >= 0", name="ck_inventory_damaged_non_negative"),
        # Reserved stock can never exceed what is physically available+reserved on hand.
        CheckConstraint(
            "reserved_quantity <= available_quantity + reserved_quantity",
            name="ck_inventory_reserved_le_total",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False)

    # available_quantity = physically on-hand and NOT reserved (sellable now)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    damaged_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product = relationship("Product", back_populates="inventory_records")
    warehouse = relationship("Warehouse", back_populates="inventory_records")

    @property
    def total_on_hand(self) -> int:
        return self.available_quantity + self.reserved_quantity


class InventoryTransaction(Base):
    """Immutable ledger entry. Never updated or deleted after creation -
    enforced at the application/service layer (no update/delete service methods
    are exposed for this model) and by restricting DB grants in production.
    """

    __tablename__ = "inventory_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    transaction_type: Mapped[InventoryTransactionType] = mapped_column(
        Enum(InventoryTransactionType), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # signed: +in / -out
    previous_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    new_quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    reference_entity: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "order"
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
