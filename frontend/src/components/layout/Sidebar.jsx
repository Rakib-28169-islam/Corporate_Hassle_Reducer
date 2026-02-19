import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Wrench, MessageSquare } from 'lucide-react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/tools', icon: Wrench, label: 'Tools' },
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
]

export default function Sidebar() {
  return (
    <aside className="w-64 bg-slate-800/50 border-r border-slate-700 flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="p-6 border-b border-slate-700">
        <h1 className="text-lg font-bold text-white tracking-tight">
          Corporate Hassle
          <span className="text-blue-500"> Reducer</span>
        </h1>
        <p className="text-xs text-slate-500 mt-1">AI-Powered Workspace</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-4 space-y-1">
        {navItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${
                isActive
                  ? 'bg-blue-600/20 text-blue-400 border border-blue-500/30'
                  : 'text-slate-400 hover:text-white hover:bg-slate-700/50'
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-slate-700">
        <div className="flex items-center gap-3 px-4 py-2">
          <div className="h-8 w-8 rounded-full bg-blue-600/20 border border-blue-500/30 flex items-center justify-center">
            <span className="text-blue-400 text-sm font-medium">U</span>
          </div>
          <div>
            <p className="text-sm text-white font-medium">Default User</p>
            <p className="text-xs text-slate-500">user_id: default</p>
          </div>
        </div>
      </div>
    </aside>
  )
}
