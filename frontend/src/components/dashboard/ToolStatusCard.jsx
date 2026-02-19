import { Mail, Hash, Calendar, CheckCircle2, XCircle } from 'lucide-react'
import Card from '../ui/Card'
import { TOOLS } from '../../utils/constants'

const iconMap = { Mail, Hash, Calendar }

export default function ToolStatusCard({ toolName, connection }) {
  const tool = TOOLS[toolName]
  const Icon = iconMap[tool.icon]
  const isActive = connection.status === 'ACTIVE'

  return (
    <Card className="flex items-center gap-4">
      <div className={`h-12 w-12 rounded-xl ${tool.bgColor} border ${tool.borderColor} flex items-center justify-center shrink-0`}>
        <Icon size={22} className={tool.color} />
      </div>
      <div className="flex-1 min-w-0">
        <h3 className="text-white font-medium">{tool.name}</h3>
        <p className="text-xs text-slate-400 truncate">
          {isActive ? 'Connected and ready' : 'Not connected'}
        </p>
      </div>
      {isActive ? (
        <CheckCircle2 size={20} className="text-green-400 shrink-0" />
      ) : (
        <XCircle size={20} className="text-slate-500 shrink-0" />
      )}
    </Card>
  )
}
