import { useState, useCallback } from 'react'
import { Mail, Hash, Calendar, ExternalLink, Unplug } from 'lucide-react'
import Button from '../ui/Button'
import Card from '../ui/Card'
import ConnectionStatus from './ConnectionStatus'
import usePolling from '../../hooks/usePolling'
import { TOOLS } from '../../utils/constants'

const iconMap = { Mail, Hash, Calendar }

export default function ToolConnectCard({ toolName, connection, onConnect, onDisconnect, onUpdate }) {
  const [loading, setLoading] = useState(false)
  const [pollingId, setPollingId] = useState(null)
  const tool = TOOLS[toolName]
  const Icon = iconMap[tool.icon]
  const isActive = connection.status === 'ACTIVE'
  const isPolling = pollingId !== null && connection.status === 'INITIATED'

  const handleStatusChange = useCallback((data) => {
    onUpdate(toolName, data)
    if (data.status === 'ACTIVE' || data.status === 'FAILED') {
      setPollingId(null)
    }
  }, [toolName, onUpdate])

  usePolling(pollingId, handleStatusChange)

  const handleConnect = async () => {
    setLoading(true)
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
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className={`border ${isActive ? 'border-green-500/30' : 'border-slate-700'} transition-all duration-300`}>
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
    </Card>
  )
}
