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
  "problem": "个人知识管理碎片化…",
  "user_reviews": null,
  "marketing_strategy": "模板生态 + 社区…",
  "created_at": "2026-09-08T18:00:00+08:00",
  "updated_at": "2026-09-08T18:05:00+08:00",
  "categories": [
    { "id": 3, "name": "效率工具", "description": null, "created_at": "2026-09-08T18:00:00+08:00" }
  ]
}
```

### GET /api/products
- 可选查询参数 `?category_id=<id>`:只返回打有该分类的产品
- 按 id **降序**(最新的在最前)→ `200`

### POST /api/products
- 请求体:`name` 必填;`url` ≤2048、空串按 null;`monthly_visits` 整数 ≥0;
  `category_ids: number[]` 可选(默认 `[]`)
- → `201` 返回完整对象(含分类)
- `400` 某分类 id 不存在(此时不落库)

### GET /api/products/{id}
- → `200` / `404`

### PATCH /api/products/{id}
- 请求体:任意字段缺席不改动
- `category_ids` 三态:

  | 传法 | 效果 |
  | --- | --- |
  | 缺席 | 分类**不动** |
  | `[]` | **清空**全部标签 |
  | `[1, 2]` | **替换**为这组(校验 id 存在,否则 `400` 不落库) |

- → `200` / `404` / `400`

### DELETE /api/products/{id}
- → `204` / `404`;级联清掉该产品的全部标签行

---

## 前端接口对照

`frontend/src/api/resources.ts` 与上面一一对应,统一走 `client.ts`(自动处理错误与 204)。
改后端契约时,同步改 `app/schemas.py` → `frontend/src/types.ts` → `resources.ts`。
