/** 业务资源 API:统一走 client.ts 的封装。 */

import { del, get, patch, post, postForm } from './client'
import type {
  Category,
  CategoryPayload,
  Product,
  ProductImage,
  ProductPayload,
} from '../types'

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
