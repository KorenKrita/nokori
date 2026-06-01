import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { Layout } from '@/components/Layout'
import { Dashboard } from '@/pages/Dashboard'
import { Rules } from '@/pages/Rules'
import { RuleDetail } from '@/pages/RuleDetail'
import { Retrieve } from '@/pages/Retrieve'
import { Injections } from '@/pages/Injections'
import { Extract } from '@/pages/Extract'
import { Lifecycle } from '@/pages/Lifecycle'
import { Config } from '@/pages/Config'
import { Logs } from '@/pages/Logs'

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: '/', element: <Dashboard /> },
      { path: '/rules', element: <Rules /> },
      { path: '/rules/:shortId', element: <RuleDetail /> },
      { path: '/retrieve', element: <Retrieve /> },
      { path: '/injections', element: <Injections /> },
      { path: '/extract', element: <Extract /> },
      { path: '/lifecycle', element: <Lifecycle /> },
      { path: '/config', element: <Config /> },
      { path: '/logs', element: <Logs /> },
    ],
  },
])

export function App() {
  return <RouterProvider router={router} />
}
