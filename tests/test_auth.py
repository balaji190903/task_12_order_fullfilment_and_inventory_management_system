from app.models.user import RoleName
from tests.conftest import register_and_login, auth_headers


def test_register_and_login_success(client):
    tokens, user = register_and_login(client, email="alice@example.com", role=RoleName.CUSTOMER)
    assert "access_token" in tokens
    assert "refresh_token" in tokens
    assert user["email"] == "alice@example.com"
    assert user["role"] == "customer"


def test_register_duplicate_email_rejected(client):
    register_and_login(client, email="dup@example.com", role=RoleName.CUSTOMER)
    resp = client.post(
        "/api/auth/register",
        json={"email": "dup@example.com", "password": "SecurePass123", "full_name": "Dup", "role": "customer"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DUPLICATE_EMAIL"


def test_login_invalid_credentials(client):
    register_and_login(client, email="bob@example.com", role=RoleName.CUSTOMER)
    resp = client.post("/api/auth/login", json={"email": "bob@example.com", "password": "WrongPass"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_login_nonexistent_user(client):
    resp = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert resp.status_code == 401


def test_me_requires_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(client):
    tokens, user = register_and_login(client, email="carol@example.com", role=RoleName.CUSTOMER)
    resp = client.get("/api/auth/me", headers=auth_headers(tokens["access_token"]))
    assert resp.status_code == 200
    assert resp.json()["email"] == "carol@example.com"


def test_invalid_token_rejected(client):
    resp = client.get("/api/auth/me", headers=auth_headers("not-a-real-token"))
    assert resp.status_code == 401


def test_refresh_token_rotation(client):
    tokens, _ = register_and_login(client, email="dave@example.com", role=RoleName.CUSTOMER)
    refresh_resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Old refresh token must now be revoked (rotation) - reusing it should fail.
    reuse_resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse_resp.status_code == 401


def test_logout_revokes_refresh_token(client):
    tokens, _ = register_and_login(client, email="erin@example.com", role=RoleName.CUSTOMER)
    logout_resp = client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout_resp.status_code == 204

    refresh_resp = client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_resp.status_code == 401


def test_change_password_requires_correct_current_password(client):
    tokens, _ = register_and_login(client, email="frank@example.com", role=RoleName.CUSTOMER)
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "WrongOldPass", "new_password": "NewSecurePass1"},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 422


def test_change_password_success_and_relogin(client):
    tokens, _ = register_and_login(client, email="gina@example.com", role=RoleName.CUSTOMER)
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "SecurePass123", "new_password": "NewSecurePass1"},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 204

    old_login = client.post("/api/auth/login", json={"email": "gina@example.com", "password": "SecurePass123"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"email": "gina@example.com", "password": "NewSecurePass1"})
    assert new_login.status_code == 200


def test_customer_cannot_create_product(client):
    tokens, _ = register_and_login(client, email="henry@example.com", role=RoleName.CUSTOMER)
    resp = client.post(
        "/api/products",
        json={"sku": "SKU-1", "name": "Widget", "price": 9.99},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_admin_can_create_product(client):
    tokens, _ = register_and_login(client, email="admin1@example.com", role=RoleName.ADMIN)
    resp = client.post(
        "/api/products",
        json={"sku": "SKU-2", "name": "Widget", "price": 9.99},
        headers=auth_headers(tokens["access_token"]),
    )
    assert resp.status_code == 201


def test_unauthorized_warehouse_access_denied(client):
    admin_tokens, admin_user = register_and_login(client, email="admin2@example.com", role=RoleName.ADMIN)
    wh_resp = client.post(
        "/api/warehouses",
        json={"name": "Main WH", "location": "NYC", "capacity": 1000},
        headers=auth_headers(admin_tokens["access_token"]),
    )
    warehouse_id = wh_resp.json()["id"]

    manager_tokens, _ = register_and_login(client, email="manager1@example.com", role=RoleName.WAREHOUSE_MANAGER)
    # Manager has no WarehouseUser link yet -> should be denied on inventory list for this warehouse
    resp = client.get(
        f"/api/inventory?warehouse_id={warehouse_id}",
        headers=auth_headers(manager_tokens["access_token"]),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "WAREHOUSE_ACCESS_DENIED"
