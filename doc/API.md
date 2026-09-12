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

- 业务接口(**Products / Categories / Requirements / Users / Activity / Summary**)都要登录:请求带会话 cookie
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
  "last_login_at": "2026-09-12T09:31:00+08:00",
  "last_login_ip": "203.0.113.7",
  "created_at": "2026-09-10T18:00:00+08:00",
  "updated_at": "2026-09-10T18:00:00+08:00"
}
```

`role ∈ {admin, employee}`;`password_set` 由后端派生(哈希非空 = 已设过密码);受邀未设密的员工
该字段为 `false`。

`last_login_at` / `last_login_ip` = **最后一次成功登录**的时间与来源 IP(第五版审计)。
只有 `GET /api/users` 会填这两个字段,其它返回 `User` 的端点不查它,值为 `null`。

### POST /api/auth/login
- 请求体:`{"email": "...", "password": "..."}`。邮箱大小写不敏感(服务端统一存小写)。
- → `200` 返回 `User`,并 `Set-Cookie: producthub_session`(30 天)。
- `401` 邮箱或密码不正确 / 账号已被禁用。
- **每次尝试都写一条审计记录**(成功与失败都写,失败也留 IP 与 User-Agent),
  保留 180 天,在成功登录时顺手清理。

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
- 每条带出 `last_login_at` / `last_login_ip`(**最后一次成功登录**;从未登录为 `null`)。
  账号页据此显示「最后登录」列。

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

## Requirements 需求记录(第四版)

> 需登录(`401` 未登录);需求由全员共享。

**响应形状**(单条):

```json
{
  "id": 3,
  "description": "支持把研究记录导出成 CSV",
  "detail": "背景:目前只能一条条复制…",
  "priority": "高",
  "source": "用户反馈",
  "product_type": "网站",
  "proposed_on": "2026-09-01",
  "status": "已排期",
  "estimated_days": 5,
  "due_on": "2026-09-20",
  "link_url": "https://example.com/issues/1",
  "note": null,
  "created_at": "2026-09-11T10:00:00+08:00",
  "updated_at": "2026-09-11T10:00:00+08:00"
}
```

**字段**:`description`(**需求描述**,即标题,≤255 必填)与 `detail`(**需求详情**,长文,可空)
是两个字段,列表页只显示前者。日期一律 `YYYY-MM-DD` 字符串。四个枚举字段都**存中文**、都可空:

| 字段 | 取值 |
| --- | --- |
| `priority` | 高 / 中 / 低 |
| `source` | 用户反馈 / 内部提出 / 竞品分析 / 数据分析 |
| `product_type` | 网站 / 移动 App / 小程序 / 桌面端 / 浏览器插件 / 其他 |
| `status` | 待评估 / 已排期 / 进行中 / 已完成 / 已搁置 |

产品类型是**固定枚举**,不复用「分类」表(第四版定稿)。取值表在后端 `app/schemas.py`
(`REQUIREMENT_PRIORITIES` 等四个元组),前端 `frontend/src/types.ts` 的同名 `as const` 数组与之对应。

### GET /api/requirements
按 id **降序**(最新的在最前)→ `200`。搜索由前端在结果里过滤,没有查询参数。

### POST /api/requirements
- 请求体:`description` 必填(去空白后为空 → `422`);`estimated_days` 整数 ≥0;
  `link_url` ≤2048、空串按 null;其余字段可空
- → `201` 返回完整对象;非法枚举值 / 非法日期 → `422`

### GET /api/requirements/{id}
→ `200` / `404`

### PATCH /api/requirements/{id}
- 请求体任意字段缺席不改动;清空某字段传 `null`
- → `200` / `404` / `422`

### DELETE /api/requirements/{id}
→ `204` / `404`

---

## Activity 最近动态(第四版)

> 需登录。动态由各**写端点**在成功时记一条(见「事件记录」),对象删除后事件仍在。

**响应形状**(数组元素):

```json
{
  "id": 12,
  "actor_name": "张三",
  "action": "update",
  "content_type": "requirement",
  "content_type_name": "需求",
  "title": "支持把研究记录导出成 CSV",
  "object_id": 3,
  "url": "/requirements/3",
  "created_at": "2026-09-11T10:05:00+08:00"
}
```

- `action ∈ {create, update, delete}`。
- `actor_name` 与 `title` 都是**快照**:用户改名、对象被删之后,历史动态仍显示当时的名字与标题。
- `url` 由后端按内容类型拼好(前端不拼路径);**`action="delete"` 时为 `null`** —— 对象已经没了,
  给链接只会点出 404,前端据此把该行渲染成纯文本。

### GET /api/activity
- 查询参数:`limit`(默认 20,1–100)、`offset`(默认 0,≥0);越界 → `422`
- 按 `created_at` 降序,同一秒内按 `id` 降序兜底 → `200` `ActivityEvent[]`

### 事件记录(哪些操作会产生动态)

| 操作 | 是否记动态 |
| --- | --- |
| 产品 新建 / 更新 / 删除 | ✅ |
| 需求 新建 / 更新 / 删除 | ✅ |
| 分类 新建 / 改名 / 删除 | ❌ 分类是标签不是内容,记了会把动态稀释成流水账 |
| 产品图片上传 / 删除 | ❌ 归属在产品这条动态下 |

记录点在写端点里**显式一行** `crud.record_event(...)`(删除时在 `db.delete` **之前**调用,
否则读不到标题快照)。漏记不会报错,由 `tests/test_activity.py` 逐操作断言兜住。

---

## Summary 项目汇总(第四版)

### GET /api/summary

```json
[
  { "key": "product", "name": "产品", "count": 12, "url": "/products" },
  { "key": "requirement", "name": "需求", "count": 5, "url": "/requirements" },
  { "key": "blog", "name": "博客", "count": 3, "url": "/blog" }
]
```

每种内容类型现有多少条,顺序同后端 `app/content_types.py` 的 `CONTENT_TYPES`。
**只返回已启用的类型**(会议还没做,不返回 —— 显示一张永远是 0 的卡会让人以为坏了)。
前端直接渲染这个数组、不写死有哪几种:第五版加博客时,前端一行没改。

**只数「看得见的东西」**:登记了 `published_field` 的类型(博客 = `published_at`)会多一道
`WHERE published_at IS NOT NULL`,所以**草稿不计入**博客那张卡。

---

## Comments 评论与点赞(第五版)

产品、需求、博客共用这一套。`target_type` 的合法取值来自后端
`app/content_types.py` 的 `BY_KEY` —— 传了没登记的类型是 `404`。

**评论形状**(下称 `Comment`):

```json
{
  "id": 3,
  "target_type": "product",
  "target_id": 7,
  "author_id": 2,
  "author_name": "张三",
  "body": "这个定价策略值得再挖一层",
  "parent_id": null,
  "deleted_at": null,
  "like_count": 2,
  "liked_by_me": true,
  "replies": [],
  "created_at": "2026-09-12T10:20:00+08:00"
}
```

- `author_name` 是**当前**姓名(后端按 `author_id` join 出来的)。账号被删之后
  `author_id` 变 `null`,这时回落到写入时记下的那个名字。
- `deleted_at` 非空 = **墓碑**:`body` 已是空串,前端渲染成「该评论已删除」。
- `replies` 里放的是同一形状的回复,但**回复的 `replies` 恒为空数组**(只有一层)。

### GET /api/comments?target_type=&target_id=

- → `200` `Comment[]`,**只含顶层评论**,回复嵌在各自的 `replies` 里。
- 顶层按 `created_at` 正序。`limit`(默认 200,1–500)限制的是**顶层条数**,
  回复跟着父评论一起返回。
- `404` 内容类型不存在 / 评论对象不存在。**博客草稿也走这里** —— 草稿不算内容,
  对它点评跟对不存在的帖子点评是一回事。

### POST /api/comments

- 请求体:`{"target_type": "...", "target_id": 1, "body": "...", "parent_id": null}`。
  `body` 1–5000 字(去空白后不能为空)。
- → `201` `Comment`。带 `parent_id` 就是回复。
- `404` 目标不存在;`422` 父评论不存在 / 父评论不在同一个对象上 / **父评论是回复**
  (只允许一层)/ 父评论已被删除。

### DELETE /api/comments/{id}

- 作者本人或管理员 → `204`;否则 `403`。删除是**幂等**的。
- **不是硬删**:正文清空、`deleted_at` 打点,行留着 —— 硬删一条顶层评论会连带删掉
  **别人写的**回复。回复本身照常显示。

### POST / DELETE /api/comments/{id}/like

- 点赞 / 取消点赞,都是 `204`,都**幂等**(重复点不报错、不重复计数)。
- `404` 评论不存在;`422` 评论已删除(墓碑不能被赞)。

**评论不记首页动态,也不登记为内容类型** —— 照「分类不记」的先例:评论是附着在内容上的
互动,记了会把动态冲成流水账。

---

## Blog 博客(第五版)

帖子是一篇长文,带标签,可存草稿、可点赞、可点评(点评复用上面那套 Comments)。

**帖子形状**(下称 `Post`):

```json
{
  "id": 4,
  "title": "为什么我们把点评做成了多态的",
  "body": "正文…",
  "status": "published",
  "published_at": "2026-09-12T10:20:00+08:00",
  "author_id": 2,
  "author_name": "张三",
  "tags": [{ "id": 1, "name": "技术", "created_at": "..." }],
  "like_count": 2,
  "liked_by_me": true,
  "created_at": "2026-09-11T09:00:00+08:00",
  "updated_at": "2026-09-12T10:20:00+08:00"
}
```

- **`status` 是派生字段**,库里没有这一列:后端按 `published_at` 有没有值算出
  `"draft"` / `"published"`。所以它**只读** —— 想发布就 PATCH `{"status": "published"}`,
  不要自己拼 `published_at`。
- `published_at` **只在首次发布时写一次**,之后编辑不动它。「发布时间」不是「更新时间」。
- `author_name` 规则同评论:当前姓名,账号被删之后回落到写入时的快照。

### GET /api/blog

- 可选 `?tag_id=`,按标签筛(唯一一个走后端 SQL 的筛选条件)。
- → `200` `Post[]`,按 `COALESCE(published_at, created_at)` 倒序 ——
  已发布的按发布时间,草稿按创建时间(它在创建那天出现)。
- **只返回已发布的 + 自己的草稿**;管理员拿得到所有人的草稿。
- 搜索与状态筛选由前端做(量级在千级以内),不走查询参数。

### POST /api/blog

- 请求体:`{"title": "...", "body": "...", "tag_ids": [1], "status": "draft"}`。
  `title` 必填 1–255 字;`body` 上限 20000 字;`status` 缺省 `"draft"`。
- 直接带 `{"status": "published"}` 就是**写完就发**,不用先建草稿再发一次。
- → `201` `Post`。`400` 标签 id 不存在。

### GET /api/blog/{id}

- → `200` `Post`。
- **`404` 不只表示「不存在」,也表示「是别人的草稿」** —— 对没有权限的人来说,
  草稿就是不存在的。这里刻意**不给 `403`**:403 等于承认「有这么一篇」。
  (已发布但属于别人的帖子是看得见的,那种情况改不动才回 `403`,见 PATCH。)

### PATCH /api/blog/{id}

- 请求体字段全部可选,缺席即不改;`tag_ids` 缺席=不动 / `[]`=清空 / `[id]`=整组替换。
- `{"status": "published"}` = **首次发布**:写 `published_at`;
  已经发布过的再传一次是空操作(时间保持第一次那个)。
- `403` 帖子看得见但不是你的(作者本人或管理员才能改)。
- `422` 对**已发布的**帖子传 `status="draft"` —— 退不回草稿。
  「发布时间」是一个已经发生的事实,退回去就得把它抹掉;真要撤回发布,那是删除的事。

### DELETE /api/blog/{id}

- 作者本人或管理员 → `204`;`403` / `404` 规则同上。
- 连带清掉它的**点评与帖子点赞**(`comments` 上没有指向帖子的外键,这笔账在
  路由里手动结,见 `crud.delete_comments_for`)。

### POST / DELETE /api/blog/{id}/like

- 帖子点赞 / 取消,都是 `204`,都**幂等**。`liked_by_me` 是「**我**赞没赞」,
  所以同一篇帖子在不同人眼里这个字段不同。

### 标签 `/api/blog/tags`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/blog/tags` | → `200` `BlogTag[]`(按 id 升序) |
| POST | `/api/blog/tags` | `{"name": "..."}` → `201`;`409` 重名 |
| PATCH | `/api/blog/tags/{id}` | `{"name": "..."}` → `200`;`409` 重名 |
| DELETE | `/api/blog/tags/{id}` | → `204`。**帖子保留**,只是摘掉这个标签 |

> ⚠️ **这四条在 `routers/blog.py` 里必须声明在 `/blog/{post_id}` 之前**。FastAPI 按声明顺序
> 匹配,顺序被挪了的话 `/blog/tags` 会先落到 `{post_id}` 头上,拿 `"tags"` 转 int 得 `422`。
> `tests/test_blog.py::test_tags_route_is_not_swallowed_by_post_id` 盯着这件事。

**标签不记动态**(照「分类不记」的先例)。

### 草稿:它会打穿「行存在 = 可见」这条假设

`content_types.py` 建立在「行存在 ⇒ 该行可见」之上,这对产品 / 需求成立,**草稿不成立**。
所以有四处必须一起挡住,少挡一处就是一处泄露,而且**都不会报错**:

1. `GET /api/blog` 的列表过滤;
2. `GET /api/blog/{id}` 的 `404`;
3. `GET/POST /api/comments` 的目标校验 —— 否则 `target_type=blog&target_id=<猜>`
   能问出「有这么一篇」,还能在草稿底下评论;
4. `GET /api/summary` 的计数。

后两处读的是 `ContentType.published_field` 这一个声明式字段,**不在各处写
`if 是博客`** —— 这样「加内容类型只改一处」的性质保住了。

### 动态规则

草稿要是在动态里广播,等于把没写完的东西喊给所有人:

- 草稿的新建 / 编辑 / 删除 —— **一条都不记**;
- **首次发布**记一条 `create`(它此刻才出现在大家面前);
- 已发布的帖子再编辑记 `update`,删除记 `delete`。

---

## 前端接口对照

`frontend/src/api/resources.ts` 与上面一一对应,统一走 `client.ts`(自动处理错误与 204)。
改后端契约时,同步改 `app/schemas.py` → `frontend/src/types.ts` → `resources.ts`。

标准五件套(list / get / create / update / remove)的转发函数由 `resources.ts` 里的
`crud<T>(basePath)` 工厂生成(第四版收敛,此前有十几份逐字相同的副本)。**端点本身不抽象**:
产品列表要按 `?category_id=` 过滤、用户 PATCH 的载荷是 `UserPatchPayload`、重置密码要发邮件,
这些形状不同的照旧各自显式写,不往工厂里加 if。
