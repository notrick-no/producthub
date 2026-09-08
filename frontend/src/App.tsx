import { Suspense, lazy } from 'react'
import { Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import AppLayout from './components/AppLayout'

// 路由级懒加载:每个页面独立 chunk,首屏只加载用到的
const ProductList = lazy(() => import('./pages/ProductList'))
const ProductFormPage = lazy(() => import('./pages/ProductFormPage'))
const ProductDetail = lazy(() => import('./pages/ProductDetail'))

function PageFallback() {
  return (
    <div style={{ textAlign: 'center', padding: 80 }}>
      <Spin />
    </div>
  )
}

export default function App() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<ProductList />} />
          <Route path="/products/new" element={<ProductFormPage />} />
          <Route path="/products/:id" element={<ProductDetail />} />
          <Route path="/products/:id/edit" element={<ProductFormPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
