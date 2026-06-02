import { lazy, Suspense } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Layout } from '@/components/Layout'
import { MeshBackground } from '@/components/MeshBackground'
import { PageSkeleton } from '@/components/PageSkeleton'

const Dashboard = lazy(() => import('@/pages/Dashboard').then((m) => ({ default: m.Dashboard })))
const Rules = lazy(() => import('@/pages/Rules').then((m) => ({ default: m.Rules })))
const RuleDetail = lazy(() => import('@/pages/RuleDetail').then((m) => ({ default: m.RuleDetail })))
const Retrieve = lazy(() => import('@/pages/Retrieve').then((m) => ({ default: m.Retrieve })))
const Injections = lazy(() => import('@/pages/Injections').then((m) => ({ default: m.Injections })))
const Extract = lazy(() => import('@/pages/Extract').then((m) => ({ default: m.Extract })))
const Lifecycle = lazy(() => import('@/pages/Lifecycle').then((m) => ({ default: m.Lifecycle })))
const Config = lazy(() => import('@/pages/Config').then((m) => ({ default: m.Config })))
const Logs = lazy(() => import('@/pages/Logs').then((m) => ({ default: m.Logs })))

function Page({ children }: { children: React.ReactNode }) {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageSkeleton />}>{children}</Suspense>
    </ErrorBoundary>
  )
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <Page><Dashboard /></Page> },
      { path: '/rules', element: <Page><Rules /></Page> },
      { path: '/rules/:shortId', element: <Page><RuleDetail /></Page> },
      { path: '/retrieve', element: <Page><Retrieve /></Page> },
      { path: '/injections', element: <Page><Injections /></Page> },
      { path: '/extract', element: <Page><Extract /></Page> },
      { path: '/lifecycle', element: <Page><Lifecycle /></Page> },
      { path: '/config', element: <Page><Config /></Page> },
      { path: '/logs', element: <Page><Logs /></Page> },
    ],
  },
])

export function App() {
  return (
    <>
      <MeshBackground />
      <RouterProvider router={router} />
    </>
  )
}
