import { useEffect } from 'react'
import { CheckCircle2 } from 'lucide-react'

export default function AuthCallbackPage() {
  useEffect(() => {
    // Auto-close after 2 seconds - the opener tab is polling for status
    const timer = setTimeout(() => {
      window.close()
    }, 2000)
    return () => clearTimeout(timer)
  }, [])

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center">
      <div className="text-center">
        <div className="h-16 w-16 rounded-full bg-green-500/10 border border-green-500/30 flex items-center justify-center mx-auto mb-4">
          <CheckCircle2 size={32} className="text-green-400" />
        </div>
        <h1 className="text-xl font-semibold text-white mb-2">Authorization Complete</h1>
        <p className="text-slate-400 text-sm">This window will close automatically...</p>
        <p className="text-slate-500 text-xs mt-4">
          If it doesn't close, you can safely close it manually.
        </p>
      </div>
    </div>
  )
}
