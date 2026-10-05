from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.models.user import RoleName, User
from app.schemas.misc import ReturnCreate, ReturnOut, ReturnStatusUpdate
from app.services import return_service

router = APIRouter(prefix="/api/returns", tags=["returns"])

_approve_roles = require_roles(RoleName.ADMIN, RoleName.WAREHOUSE_MANAGER)


@router.post("", response_model=ReturnOut, status_code=201)
def create_return(payload: ReturnCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return return_service.create_return(db, customer=current_user, data=payload)


@router.get("/{return_id}", response_model=ReturnOut)
def get_return(return_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return_ = return_service.get_return(db, return_id)
    if current_user.role == RoleName.CUSTOMER and return_.customer_id != current_user.id:
        from app.exceptions import ForbiddenError

        raise ForbiddenError("Customers may only view their own returns")
    return return_


@router.patch("/{return_id}/status", response_model=ReturnOut, dependencies=[Depends(_approve_roles)])
def update_return_status(
    return_id: int, payload: ReturnStatusUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    return_ = return_service.get_return(db, return_id)
    return return_service.transition_return_status(db, return_=return_, to_status=payload.status, user=current_user)
