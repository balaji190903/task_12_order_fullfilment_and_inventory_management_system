from app.models.user import RoleName
from tests.conftest import register_and_login, auth_headers


def _admin_headers(client, suffix=""):
    tokens, _ = register_and_login(client, email=f"ret_admin{suffix}@example.com", role=RoleName.ADMIN)
    return auth_headers(tokens["access_token"])


def _create_order(client, admin_headers, sku, qty=20):
    product_id = client.post("/api/products", json={"sku": sku, "name": "Item", "price": 20}, headers=admin_headers).json()["id"]
    warehouse_id = client.post(
        "/api/warehouses", json={"name": f"WH-{sku}", "location": "NYC", "capacity": 1000}, headers=admin_headers
    ).json()["id"]
    client.post(
        "/api/inventory/adjust",
        json={"product_id": product_id, "warehouse_id": warehouse_id, "adjustment": qty, "reason": "Initial purchase"},
        headers=admin_headers,
    )
    cust_tokens, _ = register_and_login(client, email=f"ret_cust_{sku}@example.com", role=RoleName.CUSTOMER)
    cust_headers = auth_headers(cust_tokens["access_token"])
    order_resp = client.post(
        "/api/orders",
        json={"warehouse_id": warehouse_id, "items": [{"product_id": product_id, "quantity": 2}]},
        headers=cust_headers,
    )
    order = order_resp.json()
    return order, cust_headers, product_id, warehouse_id


def test_create_return_request(client):
    admin_headers = _admin_headers(client, "1")
    order, cust_headers, product_id, warehouse_id = _create_order(client, admin_headers, "SKU-RET-1")
    order_item_id = order["items"][0]["id"]

    resp = client.post(
        "/api/returns",
        json={"order_id": order["id"], "reason": "Wrong size", "items": [{"order_item_id": order_item_id, "quantity": 1}]},
        headers=cust_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "requested"


def test_return_approval_flow(client):
    admin_headers = _admin_headers(client, "2")
    order, cust_headers, product_id, warehouse_id = _create_order(client, admin_headers, "SKU-RET-2")
    order_item_id = order["items"][0]["id"]

    return_resp = client.post(
        "/api/returns",
        json={"order_id": order["id"], "items": [{"order_item_id": order_item_id, "quantity": 1}]},
        headers=cust_headers,
    )
    return_id = return_resp.json()["id"]

    approve_resp = client.patch(f"/api/returns/{return_id}/status", json={"status": "approved"}, headers=admin_headers)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"


def test_return_rejection(client):
    admin_headers = _admin_headers(client, "3")
    order, cust_headers, product_id, warehouse_id = _create_order(client, admin_headers, "SKU-RET-3")
    order_item_id = order["items"][0]["id"]

    return_resp = client.post(
        "/api/returns",
        json={"order_id": order["id"], "items": [{"order_item_id": order_item_id, "quantity": 1}]},
        headers=cust_headers,
    )
    return_id = return_resp.json()["id"]

    reject_resp = client.patch(f"/api/returns/{return_id}/status", json={"status": "rejected"}, headers=admin_headers)
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # Once rejected, no further transitions are allowed
    resp = client.patch(f"/api/returns/{return_id}/status", json={"status": "received"}, headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_RETURN_STATUS"


def test_invalid_return_transition_rejected(client):
    admin_headers = _admin_headers(client, "4")
    order, cust_headers, product_id, warehouse_id = _create_order(client, admin_headers, "SKU-RET-4")
    order_item_id = order["items"][0]["id"]

    return_resp = client.post(
        "/api/returns",
        json={"order_id": order["id"], "items": [{"order_item_id": order_item_id, "quantity": 1}]},
        headers=cust_headers,
    )
    return_id = return_resp.json()["id"]

    # requested -> refunded directly is not allowed
    resp = client.patch(f"/api/returns/{return_id}/status", json={"status": "refunded"}, headers=admin_headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_RETURN_STATUS"


def test_return_received_updates_inventory(client):
    admin_headers = _admin_headers(client, "5")
    order, cust_headers, product_id, warehouse_id = _create_order(client, admin_headers, "SKU-RET-5", qty=20)
    order_item_id = order["items"][0]["id"]

    inv_before = client.get(f"/api/inventory/{product_id}?warehouse_id={warehouse_id}", headers=admin_headers).json()

    return_resp = client.post(
        "/api/returns",
        json={"order_id": order["id"], "items": [{"order_item_id": order_item_id, "quantity": 1}]},
        headers=cust_headers,
    )
    return_id = return_resp.json()["id"]
    client.patch(f"/api/returns/{return_id}/status", json={"status": "approved"}, headers=admin_headers)
    client.patch(f"/api/returns/{return_id}/status", json={"status": "received"}, headers=admin_headers)

    inv_after = client.get(f"/api/inventory/{product_id}?warehouse_id={warehouse_id}", headers=admin_headers).json()
    assert inv_after["available_quantity"] == inv_before["available_quantity"] + 1


def test_return_quantity_exceeding_order_rejected(client):
    admin_headers = _admin_headers(client, "6")
    order, cust_headers, product_id, warehouse_id = _create_order(client, admin_headers, "SKU-RET-6")
    order_item_id = order["items"][0]["id"]  # ordered quantity is 2

    resp = client.post(
        "/api/returns",
        json={"order_id": order["id"], "items": [{"order_item_id": order_item_id, "quantity": 99}]},
        headers=cust_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INVALID_RETURN_STATUS"
