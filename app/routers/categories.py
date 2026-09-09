"""分类(ProductCategory 中的 Category)CRUD。

路由统一挂 /api 前缀,分类资源路径:
  GET    /api/categories      列表(按 id 升序)
  POST   /api/categories      新建
  PATCH  /api/categories/{id} 部分更新
  DELETE /api/categories/{id} 删除(产品关联记录随之级联删除)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
    try:
        db.commit()
    except IntegrityError:
        # 唯一约束冲突:name 重复
        db.rollback()
        raise HTTPException(status_code=409, detail="分类名已存在")
    db.refresh(category)
    return category


@router.patch("/categories/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: int, body: CategoryUpdate, db: Session = Depends(get_db)
):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="分类不存在")

    # 只更新请求体里真正出现的键
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(category, field, value)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="分类名已存在")
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    db.delete(category)
    db.commit()
