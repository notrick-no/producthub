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
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (
    Category,
    Product,
    ProductCategory,
    ProductImage,
    ProductMilestone,
    ProductPriceTier,
)
from ..schemas import (
    CategoryRead,
    ImageUpdate,
    MilestoneInput,
    MilestoneRead,
    PriceTierInput,
    PriceTierRead,
    ProductCreate,
    ProductImageRead,
    ProductRead,
    ProductUpdate,
)
from ..storage import MAX_IMAGE_SIZE, UPLOAD_DIR, image_ext

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
    """带分类/发展历程关联一次性取出单个产品,避免懒加载。"""
    return db.scalars(
        select(Product)
        .where(Product.id == product_id)
        .options(
            selectinload(Product.category_links).selectinload(ProductCategory.category),
            selectinload(Product.milestones),
            selectinload(Product.price_tiers),
            selectinload(Product.images),
        )
    ).first()


def _to_read(product: Product) -> ProductRead:
    """把 ORM 对象组装成响应 Schema(分类按 category_id、节点按时间稳定排序)。"""
    links = sorted(product.category_links, key=lambda link: link.category_id)
    milestones = sorted(product.milestones, key=lambda m: (m.date, m.id))
    tiers = sorted(product.price_tiers, key=lambda t: t.id)  # 保持录入顺序
    images = sorted(product.images, key=lambda i: (i.sort_order, i.id))
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
        milestones=[MilestoneRead.model_validate(m) for m in milestones],
        price_tiers=[PriceTierRead.model_validate(t) for t in tiers],
        images=[ProductImageRead.model_validate(i) for i in images],
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
        stmt.options(
            selectinload(Product.category_links).selectinload(ProductCategory.category),
            selectinload(Product.milestones),
            selectinload(Product.price_tiers),
            selectinload(Product.images),
        )
    ).all()
    return [_to_read(p) for p in products]


def _add_milestones(db: Session, product_id: int, milestones: list[MilestoneInput]) -> None:
    """把一批输入节点落库(用于新建)。"""
    for m in milestones:
        db.add(
            ProductMilestone(
                product_id=product_id,
                date=m.date,
                title=m.title,
                note=m.note,
            )
        )


def _add_price_tiers(db: Session, product_id: int, tiers: list[PriceTierInput]) -> None:
    """把一批定价档位落库(用于新建)。"""
    for t in tiers:
        db.add(
            ProductPriceTier(
                product_id=product_id,
                name=t.name,
                amount=t.amount,
                cycle=t.cycle,
                note=t.note,
            )
        )


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    category_ids = _require_categories_exist(db, body.category_ids)

    product = Product(**{field: getattr(body, field) for field in _SCALAR_FIELDS})
    db.add(product)
    db.flush()  # 先拿到 product.id

    for category_id in category_ids:
        db.add(ProductCategory(product_id=product.id, category_id=category_id))
    _add_milestones(db, product.id, body.milestones)
    _add_price_tiers(db, product.id, body.price_tiers)

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

    # 发展历程:缺席 = 不动;[] / null = 清空;数组 = 整组替换(与分类一致)
    if "milestones" in payload:
        product.milestones.clear()
        db.flush()
        for m in payload["milestones"] or []:
            product.milestones.append(
                ProductMilestone(
                    product_id=product.id,
                    date=m["date"],
                    title=m["title"],
                    note=m.get("note"),
                )
            )

    # 分级定价:同款三态(缺席 = 不动;[] = 清空;数组 = 整组替换)
    if "price_tiers" in payload:
        product.price_tiers.clear()
        db.flush()
        for t in payload["price_tiers"] or []:
            product.price_tiers.append(
                ProductPriceTier(
                    product_id=product.id,
                    name=t.get("name"),
                    amount=t.get("amount"),
                    cycle=t.get("cycle"),
                    note=t.get("note"),
                )
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


def _get_image(db: Session, product_id: int, image_id: int) -> ProductImage:
    """按产品找一张图片;不属于该产品时同样 404(不暴露它属于谁)。"""
    img = db.scalar(
        select(ProductImage).where(
            ProductImage.id == image_id, ProductImage.product_id == product_id
        )
    )
    if img is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    return img


def _disk_path(image_path: str) -> str:
    """把 DB 里的服务路径转成 uploads/ 下的文件名(带基本校验)。"""
    if not image_path.startswith("/uploads/"):
        raise HTTPException(status_code=500, detail="图片路径异常")
    name = image_path.rsplit("/", 1)[-1]
    # 落盘名是随机生成的纯文件名,不允许带路径穿越
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise HTTPException(status_code=500, detail="图片路径异常")
    return name


def _original_filename(filename: str | None) -> str | None:
    """取用户文件名的纯 basename(仅作展示,不参与落盘命名)。"""
    if not filename:
        return None
    base = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return (base or None)[:255]


@router.post("/products/{product_id}/images", response_model=ProductImageRead, status_code=201)
def upload_image(
    product_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """上传一张图片:校验类型/大小 → 落盘 uploads/ → 入库元数据。"""
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="产品不存在")

    ext = image_ext(file.content_type or "")
    if ext is None:
        raise HTTPException(status_code=400, detail="只支持 JPG / PNG / GIF / WebP 图片")

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件不能上传")
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片不能超过 10 MB")

    name = uuid4().hex + ext
    (UPLOAD_DIR / name).write_bytes(data)

    # 排到该产品当前最后一位
    last = db.scalars(
        select(ProductImage.sort_order)
        .where(ProductImage.product_id == product_id)
        .order_by(ProductImage.sort_order.desc(), ProductImage.id.desc())
        .limit(1)
    ).first()
    image = ProductImage(
        product_id=product_id,
        path=f"/uploads/{name}",
        filename=_original_filename(file.filename),
        content_type=file.content_type,
        size=len(data),
        caption=None,
        sort_order=(last + 1) if last is not None else 0,
    )
    db.add(image)
    try:
        db.commit()
    except Exception:
        (UPLOAD_DIR / name).unlink(missing_ok=True)  # 入库失败则回收文件
        raise
    db.refresh(image)
    return image


@router.patch(
    "/products/{product_id}/images/{image_id}", response_model=ProductImageRead
)
def update_image(
    product_id: int,
    image_id: int,
    body: ImageUpdate,
    db: Session = Depends(get_db),
):
    """改一张图片的说明 / 排序;缺席字段不动。"""
    image = _get_image(db, product_id, image_id)
    for field in ("caption", "sort_order"):
        if field in body.model_dump(exclude_unset=True):
            setattr(image, field, getattr(body, field))
    db.commit()
    db.refresh(image)
    return image


@router.delete("/products/{product_id}/images/{image_id}", status_code=204)
def delete_image(product_id: int, image_id: int, db: Session = Depends(get_db)):
    """删掉一行图片:先删库里的元数据行,成功后再删磁盘文件。"""
    image = _get_image(db, product_id, image_id)
    name = _disk_path(image.path)
    db.delete(image)
    db.commit()
    (UPLOAD_DIR / name).unlink(missing_ok=True)
