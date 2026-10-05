import json

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.exceptions import register_exception_handlers
from app.routers import auth, products, warehouses, inventory, orders, returns, notifications, dashboard

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "REST API for an order fulfillment platform: inventory reservations, "
        "concurrency-safe stock management, order state machines, returns, "
        "notifications, and audit logging."
    ),
    version="1.0.0",
)

register_exception_handlers(app)

# ---------------------------------------------------------------------------
# Success-wrapper middleware
# Wraps every successful (2xx) JSON response as:
#   { "success": true, "data": <original body> }
# Error responses (already shaped as { "success": false, "error": {...} })
# are left untouched.
# ---------------------------------------------------------------------------
SKIP_WRAP_PATHS = {"/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def wrap_success_response(request: Request, call_next) -> Response:
    response = await call_next(request)

    # Only wrap JSON success responses; skip docs/schema paths
    content_type = response.headers.get("content-type", "")
    if (
        request.url.path in SKIP_WRAP_PATHS
        or not content_type.startswith("application/json")
        or not (200 <= response.status_code < 300)
        or response.status_code == 204
    ):
        return response

    # Buffer and decode the body
    body_bytes = b""
    async for chunk in response.body_iterator:
        body_bytes += chunk

    try:
        original = json.loads(body_bytes)
    except Exception:
        # Not valid JSON — return as-is
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=content_type,
        )

    # If the response already contains a top-level "success" key, don't double-wrap
    if isinstance(original, dict) and "success" in original:
        wrapped = body_bytes
    else:
        wrapped = json.dumps({"success": True, "data": original}).encode()

    return Response(
        content=wrapped,
        status_code=response.status_code,
        media_type="application/json",
    )


app.include_router(auth.router)
app.include_router(products.router)
app.include_router(warehouses.router)
app.include_router(inventory.router)
app.include_router(orders.router)
app.include_router(returns.router)
app.include_router(notifications.router)
app.include_router(dashboard.router)



@app.get("/", include_in_schema=False)
def root():
    """Redirect root to the interactive API docs."""
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok", "message": "API is running"}
