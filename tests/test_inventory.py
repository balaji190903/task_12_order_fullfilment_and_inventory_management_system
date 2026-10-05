import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.user import RoleName
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.warehouse import Warehouse
from app.services import inventory_service
from app.exceptions import InsufficientInventoryError
from tests.conftest import register_and_login, auth_headers, TEST_DATABASE_URL


def _setup_product_and_warehouse(client, headers):
    product_resp = client.post("/api/products", json={"sku": "SKU-INV-1", "name": "Gadget", "price": 15}, headers=headers)
    warehouse_resp = client.post(
        "/api/warehouses", json={"name": "WH1", "location": "LA", "capacity": 500}, headers=headers
    )
    return product_resp.json()["id"], warehouse_resp.json()["id"]


def _admin(client):
    tokens, _ = register_and_login(client, email="inv_admin@example.com", role=RoleName.ADMIN)
    return auth_headers(tokens["access_token"])


def test_adjust_inventory_creates_record_if_missing(client):
    headers = _admin(client)
    product_id, warehouse_id = _setup_product_and_warehouse(client, headers)

    resp = client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": warehouse_id, "adjustment": 100, "reason": "Initial purchase"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["available_quantity"] == 100


def test_adjust_inventory_negative_result_rejected(client):
    headers = _admin(client)
    product_id, warehouse_id = _setup_product_and_warehouse(client, headers)
    client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": warehouse_id, "adjustment": 10, "reason": "Initial purchase"},
        headers=headers,
    )
    resp = client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": warehouse_id, "adjustment": -50, "reason": "damage write-off"},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"


def test_inventory_adjust_idempotency(client):
    headers = _admin(client)
    product_id, warehouse_id = _setup_product_and_warehouse(client, headers)
    payload = {
        "product_id": product_id,
        "warehouse_id": warehouse_id,
        "adjustment": 20,
        "reason": "Initial purchase",
        "idempotency_key": "adjust-key-123",
    }
    first = client.post("/api/inventory/adjust", json=payload, headers=headers)
    second = client.post("/api/inventory/adjust", json=payload, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # Confirm it was NOT applied twice (would be 40 if double-applied)
    assert first.json()["available_quantity"] == 20


def test_transfer_inventory_between_warehouses(client):
    headers = _admin(client)
    product_resp = client.post("/api/products", json={"sku": "SKU-INV-2", "name": "Gadget", "price": 15}, headers=headers)
    product_id = product_resp.json()["id"]
    wh_a = client.post("/api/warehouses", json={"name": "WH-A", "location": "LA", "capacity": 500}, headers=headers).json()["id"]
    wh_b = client.post("/api/warehouses", json={"name": "WH-B", "location": "SF", "capacity": 500}, headers=headers).json()["id"]

    client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": wh_a, "adjustment": 100, "reason": "Initial purchase"},
        headers=headers,
    )

    resp = client.post(
        "/api/inventory/transfer",
        json={"product_id": product_id, "from_warehouse_id": wh_a, "to_warehouse_id": wh_b, "quantity": 20},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["available_quantity"] == 80
    assert body["destination"]["available_quantity"] == 20


def test_transfer_insufficient_stock_rejected_and_unchanged(client):
    headers = _admin(client)
    product_resp = client.post("/api/products", json={"sku": "SKU-INV-3", "name": "Gadget", "price": 15}, headers=headers)
    product_id = product_resp.json()["id"]
    wh_a = client.post("/api/warehouses", json={"name": "WH-C", "location": "LA", "capacity": 500}, headers=headers).json()["id"]
    wh_b = client.post("/api/warehouses", json={"name": "WH-D", "location": "SF", "capacity": 500}, headers=headers).json()["id"]

    client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": wh_a, "adjustment": 10, "reason": "Initial purchase"},
        headers=headers,
    )
    resp = client.post(
        "/api/inventory/transfer",
        json={"product_id": product_id, "from_warehouse_id": wh_a, "to_warehouse_id": wh_b, "quantity": 50},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_INVENTORY_TRANSFER" or resp.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"

    # Source quantity must remain unchanged after the failed transfer
    check = client.get(f"/api/inventory/{product_id}?warehouse_id={wh_a}", headers=headers)
    assert check.json()["available_quantity"] == 10


def test_low_stock_detection(client):
    headers = _admin(client)
    product_id, warehouse_id = _setup_product_and_warehouse(client, headers)
    client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": warehouse_id, "adjustment": 5, "reason": "Initial purchase"},
        headers=headers,
    )
    # reorder_level defaults to 0, so bump available down via damage isn't needed;
    # instead directly verify the low-stock endpoint responds using default filter
    resp = client.get(f"/api/inventory/low-stock?warehouse_id={warehouse_id}", headers=headers)
    assert resp.status_code == 200
    # with reorder_level 0 and available 5, this SKU should NOT show (5 > 0)
    skus_in_result = [i["product_id"] for i in resp.json()]
    assert product_id not in skus_in_result


def test_concurrent_reservation_only_one_unit_available():
    """Real concurrency test: two threads each try to reserve the LAST unit of
    stock for the same (product, warehouse) at the same time, using separate
    DB sessions/connections (as separate concurrent HTTP requests would).
    Exactly one must succeed and the other must fail with
    InsufficientInventoryError - inventory must never go negative.
    """
    engine = create_engine(TEST_DATABASE_URL, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    setup_session = SessionLocal()
    product = Product(sku="SKU-CONCURRENCY-1", name="Contested Widget", price=10, cost_price=5)
    warehouse = Warehouse(name="Race WH", location="Somewhere", capacity=100)
    setup_session.add_all([product, warehouse])
    setup_session.flush()
    inventory = Inventory(product_id=product.id, warehouse_id=warehouse.id, available_quantity=1, reserved_quantity=0)
    setup_session.add(inventory)
    setup_session.commit()
    product_id, warehouse_id, inventory_id = product.id, warehouse.id, inventory.id
    setup_session.close()

    results = {"success": 0, "failure": 0}
    lock = threading.Lock()

    def attempt_reserve():
        session = SessionLocal()
        try:
            inventory_service.reserve_inventory(
                session,
                product_id=product_id,
                warehouse_id=warehouse_id,
                quantity=1,
                reference_entity="order",
                reference_id=None,
                user_id=None,
            )
            session.commit()
            with lock:
                results["success"] += 1
        except InsufficientInventoryError:
            session.rollback()
            with lock:
                results["failure"] += 1
        finally:
            session.close()

    t1 = threading.Thread(target=attempt_reserve)
    t2 = threading.Thread(target=attempt_reserve)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["success"] == 1
    assert results["failure"] == 1

    verify_session = SessionLocal()
    final_inventory = verify_session.get(Inventory, inventory_id)
    assert final_inventory.available_quantity == 0
    assert final_inventory.reserved_quantity == 1
    verify_session.close()
