from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Base class for all domain/business-rule errors.

    code: machine-readable error code returned to clients (e.g. "INSUFFICIENT_INVENTORY")
    message: human readable message
    status_code: HTTP status to respond with
    """

    code = "APP_ERROR"
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND


class UnauthorizedError(AppError):
    code = "UNAUTHORIZED"
    status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    code = "FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN


class ConflictError(AppError):
    code = "CONFLICT"
    status_code = status.HTTP_409_CONFLICT


class ValidationErrorApp(AppError):
    code = "VALIDATION_ERROR"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


# --- Specific domain errors named in the spec -----------------------------

class InsufficientInventoryError(ConflictError):
    code = "INSUFFICIENT_INVENTORY"


class InvalidOrderStatusError(ConflictError):
    code = "INVALID_ORDER_STATUS"


class InvalidReturnStatusError(ConflictError):
    code = "INVALID_RETURN_STATUS"


class WarehouseAccessDeniedError(ForbiddenError):
    code = "WAREHOUSE_ACCESS_DENIED"


class ProductInactiveError(ConflictError):
    code = "PRODUCT_INACTIVE"


class DuplicateIdempotencyKeyError(ConflictError):
    code = "DUPLICATE_IDEMPOTENCY_KEY"


class InvalidInventoryTransferError(ConflictError):
    code = "INVALID_INVENTORY_TRANSFER"


class DuplicateSKUError(ConflictError):
    code = "DUPLICATE_SKU"


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return _error_response(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return _error_response(
            "VALIDATION_ERROR",
            "Request validation failed",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        code_map = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}
        code = code_map.get(exc.status_code, "HTTP_ERROR")
        return _error_response(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        return _error_response(
            "INTERNAL_SERVER_ERROR",
            "An unexpected error occurred",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
