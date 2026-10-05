from app.models.user import RoleName
from tests.conftest import register_and_login, auth_headers


def _admin(client):
    tokens, _ = register_and_login(client, email="prod_admin@example.com", role=RoleName.ADMIN)
    return auth_headers(tokens["access_token"])


def test_create_product(client):
    headers = _admin(client)
    resp = client.post("/api/products", json={"sku": "SKU-100", "name": "Widget", "price": 19.99}, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["sku"] == "SKU-100"
    assert body["is_active"] is True


def test_duplicate_sku_rejected(client):
    headers = _admin(client)
    client.post("/api/products", json={"sku": "SKU-DUP", "name": "Widget", "price": 5}, headers=headers)
    resp = client.post("/api/products", json={"sku": "SKU-DUP", "name": "Widget 2", "price": 6}, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_SKU"


def test_get_and_update_product(client):
    headers = _admin(client)
    create_resp = client.post("/api/products", json={"sku": "SKU-101", "name": "Widget", "price": 10}, headers=headers)
    product_id = create_resp.json()["id"]

    get_resp = client.get(f"/api/products/{product_id}", headers=headers)
    assert get_resp.status_code == 200

    update_resp = client.put(f"/api/products/{product_id}", json={"price": 25.5}, headers=headers)
    assert update_resp.status_code == 200
    assert update_resp.json()["price"] == 25.5


def test_get_nonexistent_product_404(client):
    headers = _admin(client)
    resp = client.get("/api/products/999999", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_deactivate_product(client):
    headers = _admin(client)
    create_resp = client.post("/api/products", json={"sku": "SKU-102", "name": "Widget", "price": 10}, headers=headers)
    product_id = create_resp.json()["id"]

    resp = client.patch(f"/api/products/{product_id}/deactivate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    reactivate = client.patch(f"/api/products/{product_id}/activate", headers=headers)
    assert reactivate.status_code == 200
    assert reactivate.json()["is_active"] is True


def test_list_products_pagination(client):
    headers = _admin(client)
    for i in range(5):
        client.post("/api/products", json={"sku": f"SKU-PAGE-{i}", "name": "Widget", "price": 10}, headers=headers)

    resp = client.get("/api/products?page=1&page_size=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] >= 5
