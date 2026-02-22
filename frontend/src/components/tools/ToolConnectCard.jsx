import { useState, useCallback, useEffect, useRef } from 'react'
import { Mail, Hash, Calendar, ExternalLink, Unplug, RefreshCw, Database, CheckCircle, AlertCircle, Loader } from 'lucide-react'
import Button from '../ui/Button'
import Card from '../ui/Card'
import ConnectionStatus from './ConnectionStatus'
import usePolling from '../../hooks/usePolling'
import { syncOnConnected } from '../../api/sync'
import { TOOLS } from '../../utils/constants'
import { getUserId } from '../../utils/userId'

const iconMap = { Mail, Hash, Calendar }

// Animated progress dots
function SyncProgress({ toolName }) {
  const [dots, setDots] = useState(1)
  const [step, setStep] = useState(0)

  const steps = [
    `Connecting to ${toolName}...`,
    'Fetching your data...',
    'Storing in local database...',
    'Almost done...',
  ]

  useEffect(() => {
    const dotTimer = setInterval(() => setDots(d => (d % 3) + 1), 500)
    const stepTimer = setInterval(() => setStep(s => Math.min(s + 1, steps.length - 1)), 4000)
    return () => { clearInterval(dotTimer); clearInterval(stepTimer) }
  }, [])

  return (
    <div className="flex flex-col items-center justify-center py-6 gap-3">
      <div className="relative">
        <div className="h-12 w-12 rounded-full border-2 border-blue-500/30 flex items-center justify-center">
          <Loader size={24} className="text-blue-400 animate-spin" />
        </div>
        <div className="absolute -top-1 -right-1 h-3 w-3 bg-blue-400 rounded-full animate-ping" />
      </div>
      <div className="text-center">
        <p className="text-sm text-blue-300 font-medium">
          {steps[step]}{'.'.repeat(dots)}
        </p>
        <p className="text-xs text-slate-500 mt-1">
          This may take a moment for new accounts
        </p>
      </div>
      {/* Progress bar */}
      <div className="w-full bg-slate-700/50 rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-blue-500 to-cyan-400 rounded-full transition-all duration-1000 ease-out animate-pulse"
          style={{ width: `${Math.min(20 + step * 25, 90)}%` }}
        />
      </div>
    </div>
  )
}

// Sync result display
function SyncResultBanner({ result, onDismiss }) {
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    if (result?.status === 'synced') {
      const timer = setTimeout(() => setVisible(false), 8000)
      return () => clearTimeout(timer)
    }
  }, [result])

  if (!visible || !result) return null

  if (result.status === 'synced') {
    return (
      <div className="flex items-center gap-2 mb-3 px-3 py-2.5 bg-green-500/10 border border-green-500/20 rounded-lg animate-fade-in">
        <CheckCircle size={16} className="text-green-400 shrink-0" />
        <div className="flex-1">
          <span className="text-sm text-green-300 font-medium">
            {result.count} items synced
          </span>
          <span className="text-xs text-green-400/70 ml-1.5">
            Ready for queries
          </span>
        </div>
      </div>
    )
  }

  if (result.status === 'error') {
    return (
      <div className="flex items-center gap-2 mb-3 px-3 py-2.5 bg-red-500/10 border border-red-500/20 rounded-lg">
        <AlertCircle size={16} className="text-red-400 shrink-0" />
        <span className="text-xs text-red-400">
          Sync failed — data will load on first query
        </span>
      </div>
    )
  }

  return null
}

export default function ToolConnectCard({ toolName, connection, onConnect, onDisconnect, onUpdate }) {
  const [loading, setLoading] = useState(false)
  const [pollingId, setPollingId] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState(null)
  const tool = TOOLS[toolName]
  const Icon = iconMap[tool.icon]
  const isActive = connection.status === 'ACTIVE'
  const isPolling = pollingId !== null && connection.status === 'INITIATED'

  const handleStatusChange = useCallback(async (data) => {
    onUpdate(toolName, data)
    if (data.status === 'ACTIVE') {
      setPollingId(null)
      // Trigger initial data sync after OAuth completes
      setSyncing(true)
      setSyncResult(null)
      try {
        const result = await syncOnConnected(toolName, getUserId(), data.id)
        setSyncResult(result)
      } catch (err) {
        setSyncResult({ status: 'error', count: 0, error: err.message })
      } finally {
        setSyncing(false)
      }
    } else if (data.status === 'FAILED') {
      setPollingId(null)
    }
  }, [toolName, onUpdate])

  usePolling(pollingId, handleStatusChange)

  const handleConnect = async () => {
    setLoading(true)
    setSyncResult(null)
    try {
      const data = await onConnect(toolName)
      setPollingId(data.connection_id)
      // Open OAuth URL in new tab
      window.open(data.redirect_url, '_blank')
    } catch {
      // Error handled in useAuth
    } finally {
      setLoading(false)
    }
  }

  const handleDisconnect = async () => {
    setLoading(true)
    try {
      await onDisconnect(toolName)
      setPollingId(null)
      setSyncResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className={`border ${isActive ? 'border-green-500/30' : syncing ? 'border-blue-500/30' : 'border-slate-700'} transition-all duration-300 relative overflow-hidden`}>
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`h-10 w-10 rounded-lg ${tool.bgColor} border ${tool.borderColor} flex items-center justify-center`}>
            <Icon size={20} className={tool.color} />
          </div>
          <div>
            <h3 className="text-white font-semibold">{tool.name}</h3>
            <p className="text-xs text-slate-400 mt-0.5">{tool.description}</p>
          </div>
        </div>
      </div>

      {/* Syncing — full loading UI */}
      {syncing && <SyncProgress toolName={tool.name} />}

      {/* Sync result banner */}
      {!syncing && <SyncResultBanner result={syncResult} />}

      {/* Footer: status + action button */}
      {!syncing && (
        <div className="flex items-center justify-between mt-4">
          <ConnectionStatus status={connection.status} isPolling={isPolling} />

          {isActive ? (
            <Button variant="danger" onClick={handleDisconnect} loading={loading}>
              <Unplug size={14} />
              Disconnect
            </Button>
          ) : (
            <Button onClick={handleConnect} loading={loading || isPolling}>
              <ExternalLink size={14} />
              {isPolling ? 'Waiting...' : 'Connect'}
            </Button>
          )}
        </div>
      )}

      {/* Subtle glow effect while syncing */}
      {syncing && (
        <div className="absolute inset-0 pointer-events-none rounded-xl border border-blue-500/20 shadow-[0_0_15px_rgba(59,130,246,0.1)]" />
      )}
    </Card>
  )
}
