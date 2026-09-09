# producthub API

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
| 401 | 未登录 / 会话失效 / 账号被禁用(见「认证」) |
| 403 | 已登录但无权限(改密门禁未过 / 非管理员) |
| 404 | 资源不存在 |
| 409 | 唯一约束冲突(分类名 / 产品 url / 邮箱重复) |
| 422 | 请求体校验失败(Pydantic) |
| 503 | 邮件(SMTP)未配置或发送失败(建号 / 重置时) |

**错误体**:非 2xx 一律返回 `{"detail": "<原因>"}`,`detail` 为可直接展示的中文文案。

**PATCH 语义**:只更新请求体里出现的键;缺席键一律不改动。

**健康检查**:`GET /api/health` → `{"status": "ok"}`,公开。

### 认证(第三版)

- 业务接口(**Products / Categories / Users**)都要登录:请求带会话 cookie
  `producthub_session`(登录时 `Set-Cookie`,`HttpOnly + SameSite=Lax`,仅 https 站点带 `Secure`)。
  未登录一律 `401`;被管理员禁用 → `401`;登录但未过改密门禁 / 非管理员 → `403`。
- `Auth` 一节内的登录/登出/改密接口,以及 `/api/health`、`/uploads`(产品图片)保持公开。
- **前端无需手工管理 token**:cookie 由浏览器自动携带,`client.ts` 收到 401 会清登录态并跳登录页。
- 没有公开注册页。**第一个管理员**由启动引导自动创建(库空 + env `ADMIN_EMAIL/ADMIN_PASSWORD`,
  见 `doc/部署.md`);后续员工账号由管理员在本界面创建并邮件邀请。

---

## Auth 账号与登录

**用户响应形状**(下称 `User`;`UserRead`,不含任何密码/邀请 token 字段):

```json
{
  "id": 1,
  "email": "zhang@example.com",
  "name": "张三",
  "department": "市场部",
  "role": "admin",
  "is_active": true,
  "must_change_password": false,
  "password_set": true,
  "created_at": "2026-09-10T18:00:00+08:00",
  "updated_at": "2026-09-10T18:00:00+08:00"
}
```

`role ∈ {admin, employee}`;`password_set` 由后端派生(哈希非空 = 已设过密码);受邀未设密的员工
该字段为 `false`。

### POST /api/auth/login
- 请求体:`{"email": "...", "password": "..."}`。邮箱大小写不敏感(服务端统一存小写)。
- → `200` 返回 `User`,并 `Set-Cookie: producthub_session`(30 天)。
- `401` 邮箱或密码不正确 / 账号已被禁用。

### POST /api/auth/logout
- → `204`,删除当前会话并清 cookie(会话已失效也返回 204)。

### GET /api/auth/me
- 宽松读取当前登录用户 → `200` `User`;未登录 / 会话失效 / 被禁用 → `401`。
  前端据此在刷新页面后恢复登录态。

### POST /api/auth/password
- 请求体:`{"old_password": "...", "new_password": "..."}`,`new_password` 8–72 位。
- 改自己密码:保留当前会话,**删除该账号的其它会话**(其它登录端会被登出)。
  若 `must_change_password=true`(被重置过密码),改完自动清除该标记。
- → `200` / `400` 原密码不正确。

### POST /api/auth/set-password(公开,邀请设密)
- 请求体:`{"token": "<邮件链接里的邀请码>", "new_password": "..."}`,token 一次性、72h。
- → `200` 返回 `User`,并自动登录(`Set-Cookie`);前端设完直接进首页。
- `400` 邀请链接无效或已过期。

---

## Users 员工账号(仅管理员)

**整个 `/api/users` 只允许 `role=admin`**:非管理员 → `403`,未登录 → `401`。

### GET /api/users
- 全部账号(含禁用、含受邀未设密的),按 id 升序 → `200` `User[]`。

### POST /api/users(创建 + 邀请)
- 请求体:`{"name": "...", "email": "...", "department": "可选"}`,`name` ≤255 必填,`email` ≤255。
- 建一个 `role=employee` 的启用账号,生成一次性邀请 token(72h)并发**邀请邮件**。
- → `201` 返回 `User`(`password_set=false`;受邀者设密前**无法登录**)。
- `409` 该邮箱已注册(大小写不敏感);`422` 邮箱格式不合法 / 名字为空。
- `503` SMTP 未配置或发信失败 —— **此时不落库**,不留半截账号。

### PATCH /api/users/{id}
- 请求体任意缺席:本期支持 `name` / `department`(传 `null` 清空)/ `is_active`。
- 停用(`is_active=false`)效果 = 离职冻结:该账号**所有会话立即失效**(踢下线),无法再登录。
- 护栏:`400` 不能把**最后一个启用中的管理员**禁用(避免管理员全体锁死)。邮箱与 role 不可改。
- → `200` `User` / `404` 账号不存在。

### POST /api/users/{id}/reset-password
- 重置为随机**临时密码**并发送邮件 → 该账号 `must_change_password=true`(首次登录强制改密)+
  **删除全部会话**。
- → `200` `{"ok": true, "email": "..."}` / `404` / `400`(最后一个启用中的管理员)/ `503`(SMTP 未配置)。

---

## Categories 分类

> 需登录(`401` 未登录);分类由全员共享。

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

> 需登录(`401` 未登录);产品记录由全员共享(登录只做门禁,不区分归属)。

**响应形状**(单条,`categories` 为完整对象列表):

```json
{
  "id": 10,
  "name": "Notion",
  "url": "notion.so",
  "founder": "Ivan Zhao",
  "monthly_visits": 120000,
  "status": "成长期",
  "problem": "个人知识管理碎片化…",
  "user_reviews": null,
  "marketing_strategy": "模板生态 + 社区…",
  "tech_analysis": "React + Serverless…",
  "created_at": "2026-09-08T18:00:00+08:00",
  "updated_at": "2026-09-08T18:05:00+08:00",
  "categories": [
    { "id": 3, "name": "效率工具", "description": null, "created_at": "2026-09-08T18:00:00+08:00" }
  ],
  "price_tiers": [
    { "id": 1, "name": "Pro", "amount": 120, "cycle": "年", "note": "含高级功能", "created_at": "2026-09-08T18:00:00+08:00" }
  ],
  "images": [
    { "id": 2, "path": "/uploads/ab12cd.png", "filename": "shot.png", "content_type": "image/png", "size": 1518, "created_at": "2026-09-08T18:00:00+08:00" }
  ]
}
```

### GET /api/products
- 可选查询参数 `?category_id=<id>`:只返回打有该分类的产品
- 按 id **降序**(最新的在最前)→ `200`

### POST /api/products
- 请求体:`name` 必填;`url` ≤2048、空串按 null;`monthly_visits` 整数 ≥0;
  `status` 四选一(生命周期:萌芽期/成长期/成熟期/衰退期,可空);
  `tech_analysis` 可空、空串按 null;`category_ids: number[]` 可选(默认 `[]`);
  `price_tiers: PriceTierInput[]` 可选(默认 `[]`)
- PriceTierInput = `{ name?, amount?, cycle?, note? }`:全字段可选、空串按 null;
  `amount` 数字 ≥0(存两位小数;`0` = 免费,空 = 面议/定制);`name` ≤100;`cycle` ≤20
  (常用 月/年/一次性,不强制枚举);`note` 备注/币种。任一档字段校验失败整体 `422`
- → `201` 返回完整对象(含分类、定价档位;`images` 为只读元数据,默认 `[]`,
  图片只能通过下方图片端点上传)
- `400` 某分类 id 不存在(此时不落库)

### GET /api/products/{id}
- → `200` / `404`;读回的产品里 `price_tiers` 按 id **录入顺序**、
  `images` 按 id **录入顺序**排列(图片无排序字段)

### PATCH /api/products/{id}
- 请求体:任意字段缺席不改动
- `status` 只接受四档取值(生命周期四档),非法值或空串 → `422`;清空用 `null`
- `category_ids` 三态:

  | 传法 | 效果 |
  | --- | --- |
  | 缺席 | 分类**不动** |
  | `[]` | **清空**全部标签 |
  | `[1, 2]` | **替换**为这组(校验 id 存在,否则 `400` 不落库) |

- `price_tiers` 同款三态:缺席 = 不动;`[]` = 清空;数组 = **整组替换**(校验失败 `422`,任何改动都不落库)
- → `200` / `404` / `400`

### DELETE /api/products/{id}
- → `204` / `404`;级联清掉该产品的全部标签行、定价档位与图片元数据行
  (图片的磁盘文件此时**不自动删**——单机工具,产品删除后残留文件可手动清理)

### 图片子资源 `/api/products/{id}/images`(产品素材)

图片文件本体存项目根 `uploads/`(以 `/uploads` 静态托管),**数据库只存元数据**,响应里的
`path` 即浏览器可直接加载的图片地址。上传为 multipart 表单;支持 `image/jpeg|png|gif|webp`,
单张 ≤10 MB。图片**没有说明、没有排序**字段,展示顺序即录入顺序(按 id)。

#### POST /api/products/{id}/images
- 请求体:multipart,字段名 `file`(文件名取纯 basename,仅展示用)
- → `201` 单张图片对象
- `400` 非图片类型 / 空文件 / 超过 10 MB;`404` 产品不存在(不落盘、不落库)

#### DELETE /api/products/{id}/images/{image_id}
- → `204`;先删元数据行,**成功后删除 uploads/ 里对应文件** / `404`

---

## 前端接口对照

`frontend/src/api/resources.ts` 与上面一一对应,统一走 `client.ts`(自动处理错误与 204)。
改后端契约时,同步改 `app/schemas.py` → `frontend/src/types.ts` → `resources.ts`。
