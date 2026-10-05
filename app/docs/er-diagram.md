# Entity-Relationship Diagram

Renders natively on GitHub (Mermaid). See `docs/architecture-diagram.md` for the layered request-flow diagram.

```mermaid
erDiagram
    USERS ||--o{ REFRESH_TOKENS : has
    USERS ||--o{ WAREHOUSE_USERS : "linked to"
    WAREHOUSES ||--o{ WAREHOUSE_USERS : "grants access to"
    USERS ||--o{ ORDERS : places
    USERS ||--o{ ORDER_ASSIGNMENTS : "assigned as agent"
    USERS ||--o{ NOTIFICATIONS : receives
    USERS ||--o{ AUDIT_LOGS : performs
    USERS ||--o{ IDEMPOTENCY_KEYS : owns

    CATEGORIES ||--o{ PRODUCTS : categorizes
    PRODUCTS ||--o{ INVENTORY : "stocked as"
    WAREHOUSES ||--o{ INVENTORY : stores
    PRODUCTS ||--o{ INVENTORY_TRANSACTIONS : "ledger for"
    WAREHOUSES ||--o{ INVENTORY_TRANSACTIONS : "ledger for"

    WAREHOUSES ||--o{ ORDERS : fulfills
    ORDERS ||--|{ ORDER_ITEMS : contains
    PRODUCTS ||--o{ ORDER_ITEMS : "ordered as"
    ORDERS ||--o{ ORDER_STATUS_HISTORY : tracks
    ORDERS ||--o{ ORDER_ASSIGNMENTS : "assigned via"

    ORDERS ||--o{ RETURNS : "returned via"
    RETURNS ||--|{ RETURN_ITEMS : contains
    ORDER_ITEMS ||--o{ RETURN_ITEMS : "returned from"

    USERS {
        int id PK
        string email UK
        string hashed_password
        string full_name
        enum role
        bool is_active
    }

    WAREHOUSE_USERS {
        int id PK
        int warehouse_id FK
        int user_id FK
    }

    REFRESH_TOKENS {
        int id PK
        string jti UK
        int user_id FK
        datetime expires_at
        bool revoked
    }

    CATEGORIES {
        int id PK
        string name UK
    }

    PRODUCTS {
        int id PK
        string sku UK
        string name
        int category_id FK
        numeric price
        numeric cost_price
        bool is_active
    }

    WAREHOUSES {
        int id PK
        string name
        string location
        int capacity
        bool is_active
    }

    INVENTORY {
        int id PK
        int product_id FK
        int warehouse_id FK
        int available_quantity
        int reserved_quantity
        int damaged_quantity
        int reorder_level
    }

    INVENTORY_TRANSACTIONS {
        int id PK
        int product_id FK
        int warehouse_id FK
        enum transaction_type
        int quantity
        int previous_quantity
        int new_quantity
        string reference_entity
        int reference_id
        int user_id FK
    }

    ORDERS {
        int id PK
        string order_number UK
        int customer_id FK
        int warehouse_id FK
        numeric total_amount
        enum status
        enum payment_status
        datetime expected_delivery_date
    }

    ORDER_ITEMS {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
        numeric unit_price
        numeric discount
        numeric tax
        numeric total
    }

    ORDER_STATUS_HISTORY {
        int id PK
        int order_id FK
        enum from_status
        enum to_status
        int changed_by_user_id FK
    }

    ORDER_ASSIGNMENTS {
        int id PK
        int order_id FK
        int agent_id FK
        int assigned_by_user_id FK
        bool is_active
    }

    RETURNS {
        int id PK
        int order_id FK
        int customer_id FK
        enum status
        string reason
    }

    RETURN_ITEMS {
        int id PK
        int return_id FK
        int order_item_id FK
        int quantity
    }

    NOTIFICATIONS {
        int id PK
        int user_id FK
        string event_type
        string title
        string message
        bool is_read
    }

    AUDIT_LOGS {
        int id PK
        int user_id FK
        string action
        string entity
        int entity_id
        string ip_address
    }

    IDEMPOTENCY_KEYS {
        int id PK
        int user_id FK
        string endpoint
        string key
        int response_status
    }
```

## Design notes

- **`inventory`** is one row per `(product_id, warehouse_id)` pair (unique constraint) — this row is the row-level lock target for every reservation/release/adjust/transfer operation. See `app/services/inventory_service.py` for the concurrency strategy in full.
- **`inventory_transactions`** is an append-only ledger: every change to an `inventory` row produces exactly one (or two, for transfers) transaction rows recording `previous_quantity`/`new_quantity`, so the full history of any stock level is always reconstructible and auditable independently of the current `inventory` row.
- **`order_items.unit_price`** is snapshotted at order-creation time from `products.price` and never updated afterwards, even if the product's price changes later — this is enforced simply by never writing to it again after insert.
- **`audit_logs`** and **`inventory_transactions`** are treated as immutable at the application layer: no service function ever updates or deletes rows in these tables.
