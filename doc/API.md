# Producthut API

REST 风格,统一前缀 `/api`,请求/响应均为 JSON,字段 snake_case。字段语义与校验见
`doc/架构.md` 的"字段契约"。

## 通用约定

**状态码**

| 码 | 含义 |
| --- | --- |
| 200 | 查询 / 更新成功 |
| 201 | 新建成功 |
| 204 | 删除成功(无响应体) |
| 400 | 业务校验失败(如引用了不存在的分类) |
| 404 | 资源不存在 |
| 409 | 唯一约束冲突(分类名 / 产品 url 重复) |
| 422 | 请求体校验失败(Pydantic) |

**错误体**:非 2xx 一律返回 `{"detail": "<原因>"}`,`detail` 为可直接展示的中文文案。

**PATCH 语义**:只更新请求体里出现的键;缺席键一律不改动。

**健康检查**:`GET /api/health` → `{"status": "ok"}`。

---

## Categories 分类

**响应形状**(单条):

```json
{
  "id": 1,
  "name": "AI",
  "description": null,
  "created_at": "2026-09-08T18:00:00+08:00"
}
```

### GET /api/categories
分类列表,按 id **升序**。→ `200`

### POST /api/categories
- 请求体:`{"name": "AI", "description": "可选"}`,`name` 必填,≤100 字符
- → `201` 返回完整对象
- `409` 分类名已存在

### PATCH /api/categories/{id}
- 请求体:部分字段,`name` / `description` 均可缺席
- → `200` 完整对象 / `404` 分类不存在 / `409` 分类名已存在

### DELETE /api/categories/{id}
- → `204` / `404`
- 效果:级联删除 `product_categories` 关联行,**产品记录保留**,只是失去该标签

---

## Products 产品

**响应形状**(单条,`categories` 为完整对象列表):

```json
{
  "id": 10,
  "name": "Notion",
  "url": "notion.so",
  "founder": "Ivan Zhao",
  "monthly_visits": 120000,
  "status": "快速增长",
  "problem": "个人知识管理碎片化…",
  "user_reviews": null,
  "marketing_strategy": "模板生态 + 社区…",
  "tech_analysis": "React + Serverless…",
  "created_at": "2026-09-08T18:00:00+08:00",
  "updated_at": "2026-09-08T18:05:00+08:00",
  "categories": [
    { "id": 3, "name": "效率工具", "description": null, "created_at": "2026-09-08T18:00:00+08:00" }
  ],
  "milestones": [
    { "id": 5, "date": "2024-03-01", "title": "产品成立", "note": null, "created_at": "2026-09-08T18:00:00+08:00" }
  ],
  "price_tiers": [
    { "id": 1, "name": "Pro", "amount": 120, "cycle": "年", "note": "含高级功能", "created_at": "2026-09-08T18:00:00+08:00" }
  ],
  "images": [
    { "id": 2, "path": "/uploads/ab12cd.png", "filename": "shot.png", "content_type": "image/png", "size": 1518, "caption": null, "sort_order": 0, "created_at": "2026-09-08T18:00:00+08:00" }
  ]
}
```

### GET /api/products
- 可选查询参数 `?category_id=<id>`:只返回打有该分类的产品
- 按 id **降序**(最新的在最前)→ `200`

### POST /api/products
- 请求体:`name` 必填;`url` ≤2048、空串按 null;`monthly_visits` 整数 ≥0;
  `status` 六选一(调研中/已上线/快速增长/稳定/衰退/已关闭,可空);
  `tech_analysis` 可空、空串按 null;`category_ids: number[]` 可选(默认 `[]`);
  `milestones: MilestoneInput[]` 可选(默认 `[]`);`price_tiers: PriceTierInput[]` 可选(默认 `[]`)
- MilestoneInput = `{ date, title, note? }`:`date` 传 `YYYY-MM`(自动补当月 1 号)或 `YYYY-MM-DD`;
  `title` 非空 ≤255;`note` 可空、空串按 null
- PriceTierInput = `{ name?, amount?, cycle?, note? }`:全字段可选、空串按 null;
  `amount` 数字 ≥0(存两位小数;`0` = 免费,空 = 面议/定制);`name` ≤100;`cycle` ≤20
  (常用 月/年/一次性,不强制枚举);`note` 备注/币种。任一档字段校验失败整体 `422`
- → `201` 返回完整对象(含分类、发展历程、定价档位;`images` 为只读元数据,默认 `[]`,
  图片只能通过下方图片端点上传)
- `400` 某分类 id 不存在(此时不落库)

### GET /api/products/{id}
- → `200` / `404`;读回的产品里 `milestones` 按 (date, id) **时间升序**排列,
  `price_tiers` 按 id **录入顺序**、`images` 按 (sort_order, id) **展示顺序**排列

### PATCH /api/products/{id}
- 请求体:任意字段缺席不改动
- `status` 只接受六档取值,非法值或空串 → `422`;清空用 `null`
- `category_ids` 三态:

  | 传法 | 效果 |
  | --- | --- |
  | 缺席 | 分类**不动** |
  | `[]` | **清空**全部标签 |
  | `[1, 2]` | **替换**为这组(校验 id 存在,否则 `400` 不落库) |

- `milestones` 同款三态:缺席 = 不动;`[]` = 清空;数组 = **整组替换**(校验失败 `422`,任何改动都不落库)
- `price_tiers` 同款三态:缺席 = 不动;`[]` = 清空;数组 = **整组替换**(校验失败 `422`,任何改动都不落库)
- → `200` / `404` / `400`

### DELETE /api/products/{id}
- → `204` / `404`;级联清掉该产品的全部标签行、发展历程节点、定价档位与图片元数据行
  (图片的磁盘文件此时**不自动删**——单机工具,产品删除后残留文件可手动清理)

### 图片子资源 `/api/products/{id}/images`(产品素材)

图片文件本体存项目根 `uploads/`(以 `/uploads` 静态托管),**数据库只存元数据**,响应里的
`path` 即浏览器可直接加载的图片地址。上传为 multipart 表单;支持 `image/jpeg|png|gif|webp`,
单张 ≤10 MB。

#### POST /api/products/{id}/images
- 请求体:multipart,字段名 `file`(文件名取纯 basename,仅展示用)
- → `201` 单张图片对象;新图排到该产品当前最后一位(`sort_order` 递增)
- `400` 非图片类型 / 空文件 / 超过 10 MB;`404` 产品不存在(不落盘、不落库)

#### PATCH /api/products/{id}/images/{image_id}
- 请求体:任意字段缺席不动;`caption`(空串按 null,即清空说明)/ `sort_order`(整数 ≥0)
- → `200` 更新后的图片对象 / `404` 图片不存在或不属于该产品

#### DELETE /api/products/{id}/images/{image_id}
- → `204`;先删元数据行,**成功后删除 uploads/ 里对应文件** / `404`

---

## 前端接口对照

`frontend/src/api/resources.ts` 与上面一一对应,统一走 `client.ts`(自动处理错误与 204)。
改后端契约时,同步改 `app/schemas.py` → `frontend/src/types.ts` → `resources.ts`。
