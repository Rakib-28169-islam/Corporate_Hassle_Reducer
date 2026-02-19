import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import useAuth from '../../hooks/useAuth'

export default function Layout() {
  const auth = useAuth()

  return (
    <div className="flex min-h-screen bg-slate-900">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        <TopBar activeCount={auth.activeCount} />
        <main className="flex-1 p-8">
          <Outlet context={auth} />
        </main>
      </div>
    </div>
  )
}
