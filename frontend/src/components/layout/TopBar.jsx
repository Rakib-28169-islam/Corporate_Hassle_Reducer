import { useLocation } from 'react-router-dom'
import { Activity, LogOut, User } from 'lucide-react'

const pageTitles = {
  '/': 'Dashboard',
  '/tools': 'Connected Tools',
  '/chat': 'Chat with AI',
}

export default function TopBar({ activeCount = 0, userEmail, onLogout }) {
  const { pathname } = useLocation()
  const title = pageTitles[pathname] || 'Dashboard'

  return (
    <header className="h-16 border-b border-slate-700 bg-slate-800/30 backdrop-blur-sm flex items-center justify-between px-8 sticky top-0 z-10">
      <h2 className="text-lg font-semibold text-white">{title}</h2>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-sm">
          <Activity size={16} className={activeCount > 0 ? 'text-green-400' : 'text-slate-500'} />
          <span className={activeCount > 0 ? 'text-green-400' : 'text-slate-500'}>
            {activeCount} tool{activeCount !== 1 ? 's' : ''} connected
          </span>
        </div>

        {userEmail && (
          <>
            <div className="h-4 w-px bg-slate-600" />
            <div className="flex items-center gap-2 text-sm text-slate-300">
              <User size={14} className="text-slate-400" />
              <span className="max-w-[200px] truncate">{userEmail}</span>
            </div>
            {onLogout && (
              <button
                onClick={onLogout}
                className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-red-400 transition-colors"
                title="Sign out"
              >
                <LogOut size={14} />
              </button>
            )}
          </>
        )}
      </div>
    </header>
  )
}
