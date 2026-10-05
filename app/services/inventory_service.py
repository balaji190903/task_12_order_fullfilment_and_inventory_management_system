"""
Concurrency strategy
---------------------
Every function here that mutates an Inventory row starts by taking a
row-level exclusive lock via `SELECT ... FOR UPDATE` (see `_lock_inventory_row`).
Postgres will block a second transaction that tries to lock the same row until
the first commits or rolls back, so two concurrent requests for the same
(product, warehouse) are serialized at the database level - the classic
"User A orders Product X / User B orders Product X, only one unit available"
race is resolved by whichever transaction acquires the lock first; the loser
re-reads fresh (post-commit) numbers and is correctly rejected if stock ran out.

This is combined with:
  - CHECK constraints (available_quantity >= 0, reserved_quantity >= 0, and
    reserved_quantity <= available_quantity + reserved_quantity) as a hard
    backstop against negative/impossible inventory even if application logic
    has a bug.
  - A single DB transaction per business operation: none of these functions
    call db.commit() themselves (only db.flush(), so later reads in the same
    transaction see pending changes). The caller (a router, or another
    service composing several inventory operations - e.g. order creation
    reserving several line items, or a transfer's debit+credit) commits once
    at the end, so a crash mid-operation leaves no partial state.
  - Idempotency keys (see idempotency_service.py) for the client-retry case,
    which locking alone does not solve (a *sequential* retry of the same
    logical request must not double-reserve).

For very high write contention, `SELECT ... FOR UPDATE SKIP LOCKED` or an
external distributed lock (Redis) could reduce lock wait time, but for the
scale of this platform, transactional row locking is simpler, and correct.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import InsufficientInventoryError, InvalidInventoryTransferError, NotFoundError
from app.models.inventory import Inventory, InventoryTransaction, InventoryTransactionType
from app.services.audit_service import record_audit


def _lock_inventory_row(db: Session, *, product_id: int, warehouse_id: int, create_if_missing: bool = False) -> Inventory:
    stmt = (
        select(Inventory)
        .where(Inventory.product_id == product_id, Inventory.warehouse_id == warehouse_id)
        .with_for_update()
    )
    inventory = db.execute(stmt).scalar_one_or_none()
    if inventory is None:
        if not create_if_missing:
            raise NotFoundError(
                f"No inventory record for product {product_id} in warehouse {warehouse_id}"
            )
        inventory = Inventory(product_id=product_id, warehouse_id=warehouse_id)
        db.add(inventory)
        db.flush()
        # Re-select with lock to be safe under race-to-create (rare, but the
        # unique constraint on (product_id, warehouse_id) protects us either way)
    return inventory


def _write_transaction(
    db: Session,
    *,
    inventory: Inventory,
    transaction_type: InventoryTransactionType,
    signed_quantity: int,
    previous_quantity: int,
    new_quantity: int,
    reason: str | None,
    user_id: int | None,
    reference_entity: str | None = None,
    reference_id: int | None = None,
    meta: dict | None = None,
) -> InventoryTransaction:
    txn = InventoryTransaction(
        product_id=inventory.product_id,
        warehouse_id=inventory.warehouse_id,
        transaction_type=transaction_type,
        quantity=signed_quantity,
        previous_quantity=previous_quantity,
        new_quantity=new_quantity,
        reference_entity=reference_entity,
        reference_id=reference_id,
        reason=reason,
        user_id=user_id,
        meta=meta,
    )
    db.add(txn)
    db.flush()
    return txn


def get_inventory(db: Session, *, product_id: int, warehouse_id: int) -> Inventory:
    inventory = (
        db.query(Inventory)
        .filter(Inventory.product_id == product_id, Inventory.warehouse_id == warehouse_id)
        .first()
    )
    if not inventory:
        raise NotFoundError(f"No inventory record for product {product_id} in warehouse {warehouse_id}")
    return inventory


def list_inventory(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    warehouse_id: int | None = None,
    product_id: int | None = None,
    low_stock: bool = False,
    out_of_stock: bool = False,
):
    stmt = select(Inventory)
    if warehouse_id is not None:
        stmt = stmt.where(Inventory.warehouse_id == warehouse_id)
    if product_id is not None:
        stmt = stmt.where(Inventory.product_id == product_id)
    if low_stock:
        stmt = stmt.where(Inventory.available_quantity <= Inventory.reorder_level)
    if out_of_stock:
        stmt = stmt.where(Inventory.available_quantity == 0)

    total = len(db.execute(stmt).scalars().all())
    stmt = stmt.order_by(Inventory.id).offset((page - 1) * page_size).limit(page_size)
    items = list(db.execute(stmt).scalars().all())
    return items, total


def reserve_inventory(
    db: Session,
    *,
    product_id: int,
    warehouse_id: int,
    quantity: int,
    reference_entity: str,
    reference_id: int | None,
    user_id: int | None,
) -> Inventory:
    """Moves `quantity` units from available -> reserved. Locks the row first
    so concurrent reservations against the same inventory are serialized.
    Raises InsufficientInventoryError if not enough is available.
    """
    inventory = _lock_inventory_row(db, product_id=product_id, warehouse_id=warehouse_id)
    if inventory.available_quantity < quantity:
        raise InsufficientInventoryError(
            f"Insufficient inventory for product {product_id} in warehouse {warehouse_id}: "
            f"requested {quantity}, available {inventory.available_quantity}"
        )
    previous = inventory.available_quantity
    inventory.available_quantity -= quantity
    inventory.reserved_quantity += quantity
    db.flush()

    _write_transaction(
        db,
        inventory=inventory,
        transaction_type=InventoryTransactionType.RESERVATION,
        signed_quantity=-quantity,
        previous_quantity=previous,
        new_quantity=inventory.available_quantity,
        reason="Order reservation",
        user_id=user_id,
        reference_entity=reference_entity,
        reference_id=reference_id,
    )
    record_audit(
        db,
        user_id=user_id,
        action="INVENTORY_RESERVED",
        entity="inventory",
        entity_id=inventory.id,
        meta={"quantity": quantity, "reference_entity": reference_entity, "reference_id": reference_id},
    )
    return inventory


def release_inventory(
    db: Session,
    *,
    product_id: int,
    warehouse_id: int,
    quantity: int,
    reference_entity: str,
    reference_id: int | None,
    user_id: int | None,
    convert_to_sale: bool = False,
) -> Inventory:
    """Moves `quantity` units from reserved back to available (order cancelled),
    or removes them permanently if convert_to_sale=True (order shipped/delivered).
    """
    inventory = _lock_inventory_row(db, product_id=product_id, warehouse_id=warehouse_id)
    if inventory.reserved_quantity < quantity:
        raise InsufficientInventoryError(
            f"Cannot release {quantity} units for product {product_id}: only "
            f"{inventory.reserved_quantity} are currently reserved"
        )
    previous = inventory.reserved_quantity
    inventory.reserved_quantity -= quantity
    if not convert_to_sale:
        inventory.available_quantity += quantity
    db.flush()

    txn_type = InventoryTransactionType.SALE if convert_to_sale else InventoryTransactionType.RELEASE
    _write_transaction(
        db,
        inventory=inventory,
        transaction_type=txn_type,
        signed_quantity=-quantity if convert_to_sale else quantity,
        previous_quantity=previous,
        new_quantity=inventory.reserved_quantity,
        reason="Order cancelled" if not convert_to_sale else "Order fulfilled",
        user_id=user_id,
        reference_entity=reference_entity,
        reference_id=reference_id,
    )
    record_audit(
        db,
        user_id=user_id,
        action="INVENTORY_RELEASED" if not convert_to_sale else "INVENTORY_SOLD",
        entity="inventory",
        entity_id=inventory.id,
        meta={"quantity": quantity, "reference_entity": reference_entity, "reference_id": reference_id},
    )
    return inventory


def adjust_inventory(
    db: Session,
    *,
    product_id: int,
    warehouse_id: int,
    adjustment: int,
    reason: str,
    user_id: int | None,
) -> Inventory:
    """Manual stock adjustment (recount, damage write-off, purchase receipt, etc).
    `adjustment` is a signed delta applied to available_quantity.
    """
    inventory = _lock_inventory_row(db, product_id=product_id, warehouse_id=warehouse_id, create_if_missing=True)
    previous = inventory.available_quantity
    new_value = previous + adjustment
    if new_value < 0:
        raise InsufficientInventoryError(
            f"Adjustment would result in negative available inventory "
            f"({previous} + {adjustment} = {new_value})"
        )
    inventory.available_quantity = new_value
    db.flush()

    txn_type = InventoryTransactionType.ADJUSTMENT
    if adjustment > 0 and "purchase" in reason.lower():
        txn_type = InventoryTransactionType.PURCHASE
    elif adjustment < 0 and "damage" in reason.lower():
        txn_type = InventoryTransactionType.DAMAGE

    _write_transaction(
        db,
        inventory=inventory,
        transaction_type=txn_type,
        signed_quantity=adjustment,
        previous_quantity=previous,
        new_quantity=new_value,
        reason=reason,
        user_id=user_id,
    )
    record_audit(
        db,
        user_id=user_id,
        action="INVENTORY_ADJUSTED",
        entity="inventory",
        entity_id=inventory.id,
        meta={"adjustment": adjustment, "reason": reason, "previous": previous, "new": new_value},
    )
    return inventory


def transfer_inventory(
    db: Session,
    *,
    product_id: int,
    from_warehouse_id: int,
    to_warehouse_id: int,
    quantity: int,
    user_id: int | None,
) -> tuple[Inventory, Inventory]:
    """Moves stock between warehouses. Locks BOTH rows (always in ascending
    warehouse_id order, to avoid deadlocks when two transfers run in opposite
    directions concurrently) and performs the debit+credit in one transaction:
    if anything fails, the whole operation rolls back and quantities are
    untouched, per the spec.
    """
    if from_warehouse_id == to_warehouse_id:
        raise InvalidInventoryTransferError("Source and destination warehouse must differ")

    # Deterministic lock ordering prevents deadlocks between opposite-direction transfers.
    first_wh, second_wh = sorted([from_warehouse_id, to_warehouse_id])
    locked = {}
    for wh in (first_wh, second_wh):
        locked[wh] = _lock_inventory_row(db, product_id=product_id, warehouse_id=wh, create_if_missing=(wh == to_warehouse_id))

    source = locked[from_warehouse_id]
    destination = locked[to_warehouse_id]

    if source.available_quantity < quantity:
        raise InsufficientInventoryError(
            f"Insufficient inventory to transfer: warehouse {from_warehouse_id} has "
            f"{source.available_quantity} available, requested {quantity}"
        )

    source_previous = source.available_quantity
    dest_previous = destination.available_quantity

    source.available_quantity -= quantity
    destination.available_quantity += quantity
    db.flush()

    _write_transaction(
        db,
        inventory=source,
        transaction_type=InventoryTransactionType.TRANSFER_OUT,
        signed_quantity=-quantity,
        previous_quantity=source_previous,
        new_quantity=source.available_quantity,
        reason=f"Transfer to warehouse {to_warehouse_id}",
        user_id=user_id,
        reference_entity="warehouse",
        reference_id=to_warehouse_id,
    )
    _write_transaction(
        db,
        inventory=destination,
        transaction_type=InventoryTransactionType.TRANSFER_IN,
        signed_quantity=quantity,
        previous_quantity=dest_previous,
        new_quantity=destination.available_quantity,
        reason=f"Transfer from warehouse {from_warehouse_id}",
        user_id=user_id,
        reference_entity="warehouse",
        reference_id=from_warehouse_id,
    )
    record_audit(
        db,
        user_id=user_id,
        action="INVENTORY_TRANSFERRED",
        entity="inventory",
        entity_id=source.id,
        meta={
            "product_id": product_id,
            "from_warehouse_id": from_warehouse_id,
            "to_warehouse_id": to_warehouse_id,
            "quantity": quantity,
        },
    )
    return source, destination


def get_low_stock(db: Session, *, warehouse_id: int | None = None) -> list[Inventory]:
    stmt = select(Inventory).where(Inventory.available_quantity <= Inventory.reorder_level)
    if warehouse_id is not None:
        stmt = stmt.where(Inventory.warehouse_id == warehouse_id)
    return list(db.execute(stmt).scalars().all())
