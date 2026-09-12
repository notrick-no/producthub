"""需求(Requirement)CRUD —— 第四版新增。

路由挂 /api 前缀:
  GET    /api/requirements           列表(按录入先后倒序,最新的在前)
  POST   /api/requirements           新建
  GET    /api/requirements/{id}      详情
  PATCH  /api/requirements/{id}      部分更新(请求体里没出现的键不改动)
  DELETE /api/requirements/{id}      删除

需求只有标量字段,没有子表也没有多对多,所以中间那几行走 crud 的纯函数就够,
不用像 products.py 那样手工组装响应(那个要预加载三张子表)。

字段契约见 app/schemas.py 的 RequirementBase,与 frontend/src/types.ts 一一对应。
「需求描述」是标题(必填),「需求详情」是长文;四个枚举字段存中文。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..deps import get_current_user
from ..models import Requirement, User
from ..schemas import RequirementCreate, RequirementRead, RequirementUpdate

# 需要登录(第三版);业务访问还受 must_change_password 硬门禁约束
router = APIRouter(
    prefix="/api", tags=["requirements"], dependencies=[Depends(get_current_user)]
)


@router.get("/requirements", response_model=list[RequirementRead])
def list_requirements(db: Session = Depends(get_db)):
    """列表,按录入先后倒序(最新的在前)。"""
    return db.scalars(select(Requirement).order_by(Requirement.id.desc())).all()


@router.post("/requirements", response_model=RequirementRead, status_code=201)
def create_requirement(
    body: RequirementCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requirement = Requirement(**body.model_dump())
    db.add(requirement)
    db.flush()  # 先拿到 requirement.id,记动态要用
    crud.record_event(db, user, crud.ACTION_CREATE, requirement)
    db.commit()
    db.refresh(requirement)
    return requirement


@router.get("/requirements/{requirement_id}", response_model=RequirementRead)
def get_requirement(requirement_id: int, db: Session = Depends(get_db)):
    return crud.get_or_404(db, Requirement, requirement_id, "需求")


@router.patch("/requirements/{requirement_id}", response_model=RequirementRead)
def update_requirement(
    requirement_id: int,
    body: RequirementUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requirement = crud.get_or_404(db, Requirement, requirement_id, "需求")

    # 需求整表字段都可改,不需要白名单(缺席即不改由 exclude_unset 保证)
    crud.apply_patch(requirement, body.model_dump(exclude_unset=True))
    crud.record_event(db, user, crud.ACTION_UPDATE, requirement)

    db.commit()
    db.refresh(requirement)
    return requirement


@router.delete("/requirements/{requirement_id}", status_code=204)
def delete_requirement(
    requirement_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    requirement = crud.get_or_404(db, Requirement, requirement_id, "需求")
    # 先记动态再删:提交之后就读不到它的标题了
    crud.record_event(db, user, crud.ACTION_DELETE, requirement)
    # comments 上没有指向需求的 FK,多态指针的账单在这里手动结(见 crud.delete_comments_for)
    crud.delete_comments_for(db, requirement)
    db.delete(requirement)
    db.commit()
