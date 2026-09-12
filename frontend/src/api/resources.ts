/** 业务资源 API:统一走 client.ts 的封装。 */

import { del, get, patch, post, postForm } from './client'
import type {
  ActivityEvent,
  Category,
  CategoryPayload,
  Comment,
  CommentPayload,
  LoginPayload,
  Product,
  ProductImage,
  ProductPayload,
  Requirement,
  RequirementPayload,
  SummaryItem,
  User,
  UserPatchPayload,
  UserPayload,
} from '../types'

/**
 * 标准五件套的工厂:资源路径 + 类型进去,五个转发函数出来。
 *
 * 抽的是**这批逐字相同的转发函数**(原本分散在下面各处,已有十几份),
 * 属于「稳定重复」。但**端点本身没被抽象**:像产品列表要按 ?category_id= 过滤、
 * 用户要发邀请邮件,都照旧另写一个,不往工厂里加 if ——
 * 工厂一旦开始按资源分叉,就是抽象过度。
 *
 * 全部写成箭头函数(不用 this),所以下面可以安全地 `export const x = api.get`。
 */
function crud<T, P>(basePath: string) {
  return {
    list: (): Promise<T[]> => get<T[]>(basePath),
    get: (id: number): Promise<T> => get<T>(`${basePath}/${id}`),
    create: (payload: P): Promise<T> => post<T>(basePath, payload),
    update: (id: number, payload: Partial<P>): Promise<T> =>
      patch<T>(`${basePath}/${id}`, payload),
    remove: (id: number): Promise<void> => del(`${basePath}/${id}`),
  }
}

// ---------- Auth(登录 / 登出 / 改密,HttpOnly cookie 会话) ----------
export function login(payload: LoginPayload): Promise<User> {
  return post<User>('/auth/login', payload)
}

export function logout(): Promise<void> {
  return post<void>('/auth/logout', {})
}

export function fetchMe(): Promise<User> {
  return get<User>('/auth/me')
}

export function changePassword(old_password: string, new_password: string): Promise<void> {
  return post<void>('/auth/password', { old_password, new_password })
}

/** 邀请链接设初始密码:后端设完即登录,返回当前用户。 */
export function setPassword(token: string, new_password: string): Promise<User> {
  return post<User>('/auth/set-password', { token, new_password })
}

// ---------- Users(管理员账号管理) ----------
// 没有「删除用户」端点,所以工厂的 remove 在这组里用不上(不用就不导出)。
const users = crud<User, UserPayload>('/users')

export const listUsers = users.list

export const createUser = users.create

/**
 * 不收进工厂:PATCH /users 的载荷是 UserPatchPayload(能改 is_active),
 * 和工厂的 Partial<UserPayload> 不是一回事 —— 形状不同就不是「重复」。
 */
export function updateUser(id: number, payload: UserPatchPayload): Promise<User> {
  return patch<User>(`/users/${id}`, payload)
}

/** 重置密码不是标准形状:要发邀请邮件,而且返回的不是 User。 */
export function resetUserPassword(id: number): Promise<{ ok: boolean; email: string }> {
  return post<{ ok: boolean; email: string }>(`/users/${id}/reset-password`, {})
}

// ---------- Products ----------
const products = crud<Product, ProductPayload>('/products')

/** 列表要带按分类过滤,所以不走工厂的 list,自己写一个。 */
export function listProducts(categoryId?: number): Promise<Product[]> {
  const query = categoryId ? `?category_id=${categoryId}` : ''
  return get<Product[]>(`/products${query}`)
}

export const getProduct = products.get

export const createProduct = products.create

/** PATCH 语义:没传的字段不动;category_ids: 缺席=不动 / [] =清空 / [id]=替换 */
export const updateProduct = products.update

export const deleteProduct = products.remove

// ---------- Product images(产品素材) ----------
export function uploadProductImage(productId: number, file: File): Promise<ProductImage> {
  const form = new FormData()
  form.append('file', file, file.name)
  return postForm<ProductImage>(`/products/${productId}/images`, form)
}

export function deleteProductImage(productId: number, imageId: number): Promise<void> {
  return del(`/products/${productId}/images/${imageId}`)
}

// ---------- Categories ----------
const categories = crud<Category, CategoryPayload>('/categories')

export const listCategories = categories.list

export const createCategory = categories.create

export const updateCategory = categories.update

export const deleteCategory = categories.remove

// ---------- Requirements(需求记录,第四版)----------
// 五个端点都是标准形状,整组由工厂生成。
const requirements = crud<Requirement, RequirementPayload>('/requirements')

/** 列表按录入先后倒序(最新的在前);搜索在前端做,不走后端参数。 */
export const listRequirements = requirements.list

export const getRequirement = requirements.get

export const createRequirement = requirements.create

export const updateRequirement = requirements.update

export const deleteRequirement = requirements.remove

// ---------- 首页:最近动态 / 项目汇总(第四版)----------
export function listActivity(limit = 20, offset = 0): Promise<ActivityEvent[]> {
  return get<ActivityEvent[]>(`/activity?limit=${limit}&offset=${offset}`)
}

/** 汇总条数按内容类型返回,前端直接渲染,不写死有哪几种。 */
export function fetchSummary(): Promise<SummaryItem[]> {
  return get<SummaryItem[]>('/summary')
}

// ---------- Comments 评论 / 点赞(第五版)----------
// 这组**不进 crud<T>() 工厂**:列表带 query 参数、点赞是 /like 子路径、
// 删除是 204 无响应体,形状都对不上那个工厂的五个标准端点。
// 照 product images 那几条的先例单独写。

/** 某个对象的评论树:顶层按时间正序,回复嵌在各自的顶层评论里。 */
export function listComments(targetType: string, targetId: number): Promise<Comment[]> {
  return get<Comment[]>(`/comments?target_type=${targetType}&target_id=${targetId}`)
}

export function createComment(payload: CommentPayload): Promise<Comment> {
  return post<Comment>('/comments', payload)
}

/** 删除是**墓碑**:正文清空、回复保留。作者本人或管理员。 */
export function deleteComment(id: number): Promise<void> {
  return del(`/comments/${id}`)
}

/** 点赞 / 取消点赞都是幂等的。 */
export function likeComment(id: number): Promise<void> {
  return post<void>(`/comments/${id}/like`, {})
}

export function unlikeComment(id: number): Promise<void> {
  return del(`/comments/${id}/like`)
}
