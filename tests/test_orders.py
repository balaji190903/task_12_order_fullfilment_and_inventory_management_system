from app.models.user import RoleName
from tests.conftest import register_and_login, auth_headers


def _admin_headers(client, suffix=""):
    tokens, _ = register_and_login(client, email=f"order_admin{suffix}@example.com", role=RoleName.ADMIN)
    return auth_headers(tokens["access_token"])


def _setup_stocked_product(client, admin_headers, sku, qty=50):
    product_id = client.post("/api/products", json={"sku": sku, "name": "Item", "price": 20}, headers=admin_headers).json()["id"]
    warehouse_id = client.post(
        "/api/warehouses", json={"name": f"WH-{sku}", "location": "NYC", "capacity": 1000}, headers=admin_headers
    ).json()["id"]
    client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": warehouse_id, "adjustment": qty, "reason": "Initial purchase"},
        headers=admin_headers,
    )
    return product_id, warehouse_id


def test_create_order_success_and_calculations(client):
    admin_headers = _admin_headers(client, "1")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-1")

    customer_tokens, _ = register_and_login(client, email="ord_cust1@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])

    resp = client.post(
        "/api/orders",
        json={
            "warehouse_id": warehouse_id,
            "items": [{"product_id": product_id, "quantity": 3, "discount": 2, "tax": 1}],
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    # 20 * 3 - 2 + 1 = 59
    assert body["total_amount"] == 59.0
    assert body["items"][0]["quantity"] == 3


def test_create_order_insufficient_inventory(client):
    admin_headers = _admin_headers(client, "2")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-2", qty=2)

    customer_tokens, _ = register_and_login(client, email="ord_cust2@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])

    resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 10}]},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"


def test_create_order_inactive_product_rejected(client):
    admin_headers = _admin_headers(client, "3")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-3")
    client.patch(f"/api/products/{product_id}/deactivate", headers=admin_headers)

    customer_tokens, _ = register_and_login(client, email="ord_cust3@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])
    resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "PRODUCT_INACTIVE"


def test_order_creation_reserves_inventory(client):
    admin_headers = _admin_headers(client, "4")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-4", qty=50)

    customer_tokens, _ = register_and_login(client, email="ord_cust4@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])
    client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 5}]},
        headers=headers,
    )

    inv_resp = client.get(f"/api/inventory/{product_id}?warehouse_id={warehouse_id}", headers=admin_headers)
    body = inv_resp.json()
    assert body["available_quantity"] == 45
    assert body["reserved_quantity"] == 5


def test_order_idempotent_creation(client):
    admin_headers = _admin_headers(client, "5")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-5", qty=50)

    customer_tokens, _ = register_and_login(client, email="ord_cust5@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])

    payload = {
        "warehouse_id": warehouse_id,
        "items": [{"product_id": product_id, "quantity": 5}],
        "idempotency_key": "order-key-abc",
    }
    first = client.post("/api/orders", json=payload, headers=headers)
    second = client.post("/api/orders", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["order_number"] == second.json()["order_number"]

    # Confirm inventory was only reserved once (would be 40 if double-reserved: 50-5-5)
    inv_resp = client.get(f"/api/inventory/{product_id}?warehouse_id={warehouse_id}", headers=admin_headers)
    assert inv_resp.json()["available_quantity"] == 45


def test_customer_cannot_view_others_order(client):
    admin_headers = _admin_headers(client, "6")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-6")

    cust1_tokens, _ = register_and_login(client, email="ord_cust6a@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(cust1_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    cust2_tokens, _ = register_and_login(client, email="ord_cust6b@example.com", role=RoleName.CUSTOMER)
    resp = client.get(f"/api/orders/{order_id}", headers=auth_headers(cust2_tokens["access_token"]))
    assert resp.status_code == 403


def test_valid_status_transition(client):
    admin_headers = _admin_headers(client, "7")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-7")
    customer_tokens, _ = register_and_login(client, email="ord_cust7@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(customer_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    resp = client.patch(f"/api/orders/{order_id}/status", json={"status": "confirmed"}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"


def test_invalid_status_transition_rejected(client):
    admin_headers = _admin_headers(client, "8")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-8")
    customer_tokens, _ = register_and_login(client, email="ord_cust8@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(customer_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    # pending -> delivered directly is not allowed
    resp = client.patch(f"/api/orders/{order_id}/status", json={"status": "delivered"}, headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_ORDER_STATUS"


def test_delivered_to_processing_rejected(client):
    admin_headers = _admin_headers(client, "9")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-9")
    customer_tokens, _ = register_and_login(client, email="ord_cust9@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(customer_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    for status in ["confirmed", "processing", "packed", "shipped", "delivered"]:
        r = client.patch(f"/api/orders/{order_id}/status", json={"status": status}, headers=admin_headers)
        assert r.status_code == 200, r.text

    resp = client.patch(f"/api/orders/{order_id}/status", json={"status": "processing"}, headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_ORDER_STATUS"


def test_cancel_order_releases_inventory(client):
    admin_headers = _admin_headers(client, "10")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-10", qty=50)
    customer_tokens, _ = register_and_login(client, email="ord_cust10@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 5}]},
        headers=headers,
    )
    order_id = order_resp.json()["id"]

    cancel_resp = client.post(f"/api/orders/{order_id}/cancel", headers=headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    inv_resp = client.get(f"/api/inventory/{product_id}?warehouse_id={warehouse_id}", headers=admin_headers)
    body = inv_resp.json()
    assert body["available_quantity"] == 50
    assert body["reserved_quantity"] == 0


def test_cannot_cancel_shipped_order(client):
    admin_headers = _admin_headers(client, "11")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-11")
    customer_tokens, _ = register_and_login(client, email="ord_cust11@example.com", role=RoleName.CUSTOMER)
    headers = auth_headers(customer_tokens["access_token"])
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=headers,
    )
    order_id = order_resp.json()["id"]

    for status in ["confirmed", "processing", "packed", "shipped"]:
        client.patch(f"/api/orders/{order_id}/status", json={"status": status}, headers=admin_headers)

    resp = client.post(f"/api/orders/{order_id}/cancel", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_ORDER_STATUS"


def test_customer_cannot_cancel_others_order(client):
    admin_headers = _admin_headers(client, "12")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-12")
    cust1_tokens, _ = register_and_login(client, email="ord_cust12a@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(cust1_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    cust2_tokens, _ = register_and_login(client, email="ord_cust12b@example.com", role=RoleName.CUSTOMER)
    resp = client.post(f"/api/orders/{order_id}/cancel", headers=auth_headers(cust2_tokens["access_token"]))
    assert resp.status_code == 403


def test_assign_order_to_agent(client):
    admin_headers = _admin_headers(client, "13")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-13")
    customer_tokens, _ = register_and_login(client, email="ord_cust13@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(customer_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    agent_tokens, agent_user = register_and_login(client, email="ord_agent13@example.com", role=RoleName.FULFILLMENT_AGENT)

    resp = client.post(f"/api/orders/{order_id}/assign", json={"agent_id": agent_user["id"]}, headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == agent_user["id"]


def test_assign_order_to_inactive_agent_rejected(client, db_session):
    from app.models.user import User

    admin_headers = _admin_headers(client, "14")
    product_id, warehouse_id = _setup_stocked_product(client, admin_headers, "SKU-ORD-14")
    customer_tokens, _ = register_and_login(client, email="ord_cust14@example.com", role=RoleName.CUSTOMER)
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 1}]},
        headers=auth_headers(customer_tokens["access_token"]),
    )
    order_id = order_resp.json()["id"]

    agent_tokens, agent_user = register_and_login(client, email="ord_agent14@example.com", role=RoleName.FULFILLMENT_AGENT)
    # Deactivate the agent directly via DB
    user_row = db_session.get(User, agent_user["id"])
    user_row.is_active = False
    db_session.commit()

    resp = client.post(f"/api/orders/{order_id}/assign", json={"agent_id": agent_user["id"]}, headers=admin_headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"
