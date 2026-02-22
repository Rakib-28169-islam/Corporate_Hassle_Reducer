import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import TopBar from './TopBar'
import useAuth from '../../hooks/useAuth'
import useWebSocket from '../../hooks/useWebSocket'
import { getUserId, setUserId, clearUserId } from '../../utils/userId'

function EmailGate({ onLogin }) {
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e) => {
    e.preventDefault()
    const trimmed = email.trim().toLowerCase()
    if (!trimmed || !trimmed.includes('@') || !trimmed.includes('.')) {
      setError('Please enter a valid email address')
      return
    }
    setUserId(trimmed)
    onLogin(trimmed)
  }

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-white mb-2">Corporate Hassle Reducer</h1>
          <p className="text-slate-400">Enter your email to get started</p>
        </div>
        <form onSubmit={handleSubmit} className="bg-slate-800 border border-slate-700 rounded-xl p-6 space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-slate-300 mb-1.5">
              Email Address
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => { setEmail(e.target.value); setError('') }}
              placeholder="you@example.com"
              autoFocus
              className="w-full px-4 py-2.5 bg-slate-900 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            {error && <p className="text-red-400 text-xs mt-1.5">{error}</p>}
          </div>
          <button
            type="submit"
            className="w-full py-2.5 bg-blue-600 hover:bg-blue-500 text-white font-medium rounded-lg transition-colors"
          >
            Get Started
          </button>
          <p className="text-xs text-slate-500 text-center">
            This email will be used as your identity for connecting tools and storing data.
          </p>
        </form>
      </div>
    </div>
  )
}

export default function Layout() {
  const [userId, setUserIdState] = useState(getUserId())

  const handleLogout = () => {
    clearUserId()
    setUserIdState(null)
  }

  if (!userId) {
    return <EmailGate onLogin={(email) => setUserIdState(email)} />
  }

  return <AuthenticatedLayout userId={userId} onLogout={handleLogout} />
}

function AuthenticatedLayout({ userId, onLogout }) {
  const auth = useAuth()
  const chat = useWebSocket(userId)

  return (
    <div className="flex min-h-screen bg-slate-900">
      <Sidebar />
      <div className="flex-1 flex flex-col">
        <TopBar activeCount={auth.activeCount} userEmail={userId} onLogout={onLogout} />
        <main className="flex-1 p-8">
          <Outlet context={{ ...auth, chat }} />
        </main>
      </div>
    </div>
  )
}
