"""产品(Product)CRUD + 分类打标。

路由挂 /api 前缀:
  GET    /api/products           列表(带完整 categories),可选 ?category_id= 过滤
  POST   /api/products           新建,可用 category_ids 打标(校验都存在)
  GET    /api/products/{id}      详情(带完整 categories)
  PATCH  /api/products/{id}      部分更新
  DELETE /api/products/{id}      删除(关联记录随之级联删除)

PATCH 的 category_ids 语义:
  缺席        = 不动分类
  []          = 清空分类
  [x, y]      = 校验都存在后替换成这组
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Category, Product, ProductCategory
from ..schemas import CategoryRead, ProductCreate, ProductRead, ProductUpdate

router = APIRouter(prefix="/api", tags=["products"])

# Product 上可由 API 直接写入的标量字段(与 产品.md 一致)
_SCALAR_FIELDS = [
    "name",
    "url",
    "founder",
    "monthly_visits",
    "status",
    "problem",
    "user_reviews",
    "marketing_strategy",
    "tech_analysis",
]


def _load_product(db: Session, product_id: int) -> Product | None:
    """带分类关联一次性取出单个产品,避免懒加载。"""
    return db.scalars(
        select(Product)
        .where(Product.id == product_id)
        .options(selectinload(Product.category_links).selectinload(ProductCategory.category))
    ).first()


def _to_read(product: Product) -> ProductRead:
    """把 ORM 对象组装成响应 Schema(分类按 category_id 稳定排序)。"""
    links = sorted(product.category_links, key=lambda link: link.category_id)
    return ProductRead(
        id=product.id,
        name=product.name,
        url=product.url,
        founder=product.founder,
        monthly_visits=product.monthly_visits,
        status=product.status,
        problem=product.problem,
        user_reviews=product.user_reviews,
        marketing_strategy=product.marketing_strategy,
        tech_analysis=product.tech_analysis,
        created_at=product.created_at,
        updated_at=product.updated_at,
        categories=[CategoryRead.model_validate(link.category) for link in links],
    )


def _existing_category_ids(db: Session, ids: list[int]) -> set[int]:
    """返回给定 id 里真实存在的分类 id 集合。"""
    return set(db.scalars(select(Category.id).where(Category.id.in_(ids))).all())


def _require_categories_exist(db: Session, ids: list[int]) -> list[int]:
    """校验分类 id 都存在,缺失则 400;返回去重排序后的 id。"""
    unique = sorted(set(ids))
    missing = set(unique) - _existing_category_ids(db, unique)
    if missing:
        raise HTTPException(status_code=400, detail=f"分类不存在: {sorted(missing)}")
    return unique


@router.get("/products", response_model=list[ProductRead])
def list_products(
    category_id: int | None = None, db: Session = Depends(get_db)
):
    """列表,可按分类过滤;按录入先后倒序(最新的在前)。"""
    stmt = select(Product).order_by(Product.id.desc())
    if category_id is not None:
        stmt = stmt.join(
            ProductCategory, ProductCategory.product_id == Product.id
        ).where(ProductCategory.category_id == category_id)
    products = db.scalars(
        stmt.options(selectinload(Product.category_links).selectinload(ProductCategory.category))
    ).all()
    return [_to_read(p) for p in products]


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    category_ids = _require_categories_exist(db, body.category_ids)

    product = Product(**{field: getattr(body, field) for field in _SCALAR_FIELDS})
    db.add(product)
    db.flush()  # 先拿到 product.id

    for category_id in category_ids:
        db.add(ProductCategory(product_id=product.id, category_id=category_id))

    db.commit()
    return _to_read(_load_product(db, product.id))


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = _load_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    return _to_read(product)


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int, body: ProductUpdate, db: Session = Depends(get_db)
):
    product = _load_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")

    payload = body.model_dump(exclude_unset=True)

    # 分类单独处理,不进 setattr
    if "category_ids" in payload:
        category_ids = _require_categories_exist(db, payload["category_ids"] or [])
        # 先清空旧关联(delete-orphan 会删掉旧行),再打新标,避免主键冲突
        product.category_links.clear()
        db.flush()
        for category_id in category_ids:
            product.category_links.append(
                ProductCategory(product_id=product.id, category_id=category_id)
            )

    for field in _SCALAR_FIELDS:
        if field in payload:
            setattr(product, field, payload[field])

    db.commit()
    return _to_read(_load_product(db, product.id))


@router.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    db.delete(product)
    db.commit()
