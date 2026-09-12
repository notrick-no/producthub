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

from .. import crud
from ..db import get_db
from ..deps import get_current_user
from ..models import (
    Category,
    Product,
    ProductCategory,
    ProductImage,
    ProductPriceTier,
    User,
)
from ..schemas import (
    CategoryRead,
    PriceTierInput,
    PriceTierRead,
    ProductCreate,
    ProductImageRead,
    ProductRead,
    ProductUpdate,
)
from ..storage import MAX_IMAGE_SIZE, UPLOAD_DIR, image_ext

# 整个资源需要登录(第三版);业务访问还受 must_change_password 硬门禁约束
router = APIRouter(
    prefix="/api", tags=["products"], dependencies=[Depends(get_current_user)]
)

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
    """带分类/定价/图片关联一次性取出单个产品,避免懒加载。"""
    return db.scalars(
        select(Product)
        .where(Product.id == product_id)
        .options(
            selectinload(Product.category_links).selectinload(ProductCategory.category),
            selectinload(Product.price_tiers),
            selectinload(Product.images),
        )
    ).first()


def _load_or_404(db: Session, product_id: int) -> Product:
    """同上,但取不到直接 404。

    产品要预加载三张子表,所以这里没用 crud.get_or_404(那个走 db.get,不预加载)。
    """
    product = _load_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    return product


def _to_read(product: Product) -> ProductRead:
    """把 ORM 对象组装成响应 Schema(分类按 category_id、图片按 id 稳定排序)。"""
    links = sorted(product.category_links, key=lambda link: link.category_id)
    tiers = sorted(product.price_tiers, key=lambda t: t.id)  # 保持录入顺序
    images = sorted(product.images, key=lambda i: i.id)  # 图片展示顺序 = 录入顺序
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


def _set_category_links(db: Session, product: Product, category_ids: list[int]) -> None:
    """把产品的分类标签设成这一组(多的删、少的加)。

    先清空旧关联(delete-orphan 会删掉旧行)再打新标,中间 flush 一次,
    免得新旧行撞复合主键。新建时旧关联本来就是空的,走的是同一段代码。
    """
    ids = _require_categories_exist(db, category_ids)
    product.category_links.clear()
    db.flush()
    for category_id in ids:
        product.category_links.append(
            ProductCategory(product_id=product.id, category_id=category_id)
        )


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
            selectinload(Product.price_tiers),
            selectinload(Product.images),
        )
    ).all()
    return [_to_read(p) for p in products]


def _set_price_tiers(
    db: Session, product: Product, tiers: list[PriceTierInput]
) -> None:
    """把定价档位设成这一组(整组替换)。

    与分类同款:先清空再追加,中间 flush 一次免得新旧行撞主键。
    新建时旧档位本来就是空的,走的是同一段代码。
    """
    product.price_tiers.clear()
    db.flush()
    for t in tiers:
        product.price_tiers.append(
            ProductPriceTier(
                product_id=product.id,
                name=t.name,
                amount=t.amount,
                cycle=t.cycle,
                note=t.note,
            )
        )


@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(
    body: ProductCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    product = Product(**{field: getattr(body, field) for field in _SCALAR_FIELDS})
    db.add(product)
    db.flush()  # 先拿到 product.id

    # 与 PATCH 走同一段代码:新建时旧关联是空的,「设成这一组」就等于「打这几个标」
    _set_category_links(db, product, body.category_ids)
    _set_price_tiers(db, product, body.price_tiers)

    crud.record_event(db, user, crud.ACTION_CREATE, product)
    db.commit()
    return _to_read(_load_product(db, product.id))


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return _to_read(_load_or_404(db, product_id))


@router.patch("/products/{product_id}", response_model=ProductRead)
def update_product(
    product_id: int,
    body: ProductUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    product = _load_or_404(db, product_id)

    payload = body.model_dump(exclude_unset=True)

    # 两个子集合各自整组替换,不进 setattr。
    # 「键在不在」看 payload(PATCH 三态靠它),取值用 body 上的模型对象,省得再拆字典。
    if "category_ids" in payload:
        _set_category_links(db, product, body.category_ids or [])
    if "price_tiers" in payload:
        _set_price_tiers(db, product, body.price_tiers or [])

    # 标量字段走白名单(category_ids / price_tiers 上面已单独处理)
    crud.apply_patch(product, payload, _SCALAR_FIELDS)
    crud.record_event(db, user, crud.ACTION_UPDATE, product)

    db.commit()
    return _to_read(_load_product(db, product.id))


@router.delete("/products/{product_id}", status_code=204)
def delete_product(
    product_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    product = crud.get_or_404(db, Product, product_id, "产品")
    # 先把文件名取出来(顺带校验路径):提交之后才发现路径异常的话,记录已经没了,
    # 却只能回一个 500
    image_names = [_disk_path(i.path) for i in product.images]
    # 先记动态再删:提交之后就读不到它的标题了
    crud.record_event(db, user, crud.ACTION_DELETE, product)
    # comments 上没有指向产品的 FK,多态指针的账单在这里手动结(见 crud.delete_comments_for)
    crud.delete_comments_for(db, product)
    db.delete(product)
    db.commit()
    # 提交成功之后才删磁盘文件(照 delete_image 的次序):反过来一旦回滚,
    # 就变成「图没了、记录还在」
    for name in image_names:
        (UPLOAD_DIR / name).unlink(missing_ok=True)


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
    crud.get_or_404(db, Product, product_id, "产品")

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

    image = ProductImage(
        product_id=product_id,
        path=f"/uploads/{name}",
        filename=_original_filename(file.filename),
        content_type=file.content_type,
        size=len(data),
    )
    db.add(image)
    try:
        db.commit()
    except Exception:
        (UPLOAD_DIR / name).unlink(missing_ok=True)  # 入库失败则回收文件
        raise
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
