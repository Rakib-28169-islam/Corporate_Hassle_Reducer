import { useOutletContext } from 'react-router-dom'
import ToolConnectCard from '../components/tools/ToolConnectCard'
import Spinner from '../components/ui/Spinner'
import { TOOLS } from '../utils/constants'

export default function ToolsPage() {
  const { connections, loading, connect, disconnect, updateConnection } = useOutletContext()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <p className="text-slate-400 text-sm">
          Connect your tools to let the AI assistant manage your emails, messages, and calendar.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6">
        {Object.keys(TOOLS).map((toolName) => (
          <ToolConnectCard
            key={toolName}
            toolName={toolName}
            connection={connections[toolName]}
            onConnect={connect}
            onDisconnect={disconnect}
            onUpdate={updateConnection}
          />
        ))}
      </div>
    </div>
  )
}
