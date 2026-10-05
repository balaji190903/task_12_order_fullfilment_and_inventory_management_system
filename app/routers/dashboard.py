from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles, assert_warehouse_access
from app.models.user import RoleName, User
from app.schemas.misc import AdminDashboardOut, WarehouseManagerDashboardOut, CustomerDashboardOut
from app.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/admin", response_model=AdminDashboardOut, dependencies=[Depends(require_roles(RoleName.ADMIN))])
def admin_dashboard(db: Session = Depends(get_db)):
    return dashboard_service.admin_dashboard(db)


@router.get(
    "/warehouse/{warehouse_id}",
    response_model=WarehouseManagerDashboardOut,
    dependencies=[Depends(require_roles(RoleName.ADMIN, RoleName.WAREHOUSE_MANAGER))],
)
def warehouse_dashboard(warehouse_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_warehouse_access(db, current_user, warehouse_id)
    return dashboard_service.warehouse_manager_dashboard(db, warehouse_id=warehouse_id)


@router.get(
    "/customer", response_model=CustomerDashboardOut, dependencies=[Depends(require_roles(RoleName.CUSTOMER))]
)
def customer_dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return dashboard_service.customer_dashboard(db, customer_id=current_user.id)
