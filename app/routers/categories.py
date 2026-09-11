"""分类(ProductCategory 中的 Category)CRUD。

路由统一挂 /api 前缀,分类资源路径:
  GET    /api/categories      列表(按 id 升序)
  POST   /api/categories      新建
  PATCH  /api/categories/{id} 部分更新
  DELETE /api/categories/{id} 删除(产品关联记录随之级联删除)

分类**不进首页动态流**:它是标签不是内容,记了只会把动态稀释成「新建分类」流水账。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crud
from ..db import get_db
from ..deps import get_current_user
from ..models import Category
from ..schemas import CategoryCreate, CategoryRead, CategoryUpdate

# 需要登录(第三版)
router = APIRouter(
    prefix="/api", tags=["categories"], dependencies=[Depends(get_current_user)]
)


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)):
    return db.scalars(select(Category).order_by(Category.id)).all()


@router.post("/categories", response_model=CategoryRead, status_code=201)
def create_category(body: CategoryCreate, db: Session = Depends(get_db)):
    category = Category(name=body.name, description=body.description)
    db.add(category)
    crud.commit_or_409(db, "分类名已存在")  # 唯一约束冲突:name 重复
    db.refresh(category)
    return category


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: int, body: CategoryUpdate, db: Session = Depends(get_db)
):
    category = crud.get_or_404(db, Category, category_id, "分类")

    # 只更新请求体里真正出现的键(分类整表字段都可改,不需要白名单)
    crud.apply_patch(category, body.model_dump(exclude_unset=True))

    crud.commit_or_409(db, "分类名已存在")
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = crud.get_or_404(db, Category, category_id, "分类")
    db.delete(category)
    db.commit()
