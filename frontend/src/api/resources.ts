/** 业务资源 API:统一走 client.ts 的封装。 */

import { del, get, patch, post, postForm } from './client'
import type {
  Category,
  CategoryPayload,
  LoginPayload,
  Product,
  ProductImage,
  ProductPayload,
  User,
  UserPatchPayload,
  UserPayload,
} from '../types'

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
export function listUsers(): Promise<User[]> {
  return get<User[]>('/users')
}

export function createUser(payload: UserPayload): Promise<User> {
  return post<User>('/users', payload)
}

export function updateUser(id: number, payload: UserPatchPayload): Promise<User> {
  return patch<User>(`/users/${id}`, payload)
}

export function resetUserPassword(id: number): Promise<{ ok: boolean; email: string }> {
  return post<{ ok: boolean; email: string }>(`/users/${id}/reset-password`, {})
}

// ---------- Products ----------
export function listProducts(categoryId?: number): Promise<Product[]> {
  const query = categoryId ? `?category_id=${categoryId}` : ''
  return get<Product[]>(`/products${query}`)
}

export function getProduct(id: number): Promise<Product> {
  return get<Product>(`/products/${id}`)
}

export function createProduct(payload: ProductPayload): Promise<Product> {
  return post<Product>('/products', payload)
}

/** PATCH 语义:没传的字段不动;category_ids: 缺席=不动 / [] =清空 / [id]=替换 */
export function updateProduct(id: number, payload: Partial<ProductPayload>): Promise<Product> {
  return patch<Product>(`/products/${id}`, payload)
}

export function deleteProduct(id: number): Promise<void> {
  return del(`/products/${id}`)
}

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
export function listCategories(): Promise<Category[]> {
  return get<Category[]>('/categories')
}

export function createCategory(payload: CategoryPayload): Promise<Category> {
  return post<Category>('/categories', payload)
}

export function updateCategory(id: number, payload: Partial<CategoryPayload>): Promise<Category> {
  return patch<Category>(`/categories/${id}`, payload)
}

export function deleteCategory(id: number): Promise<void> {
  return del(`/categories/${id}`)
}
