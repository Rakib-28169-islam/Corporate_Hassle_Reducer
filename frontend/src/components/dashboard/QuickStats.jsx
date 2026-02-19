import { Plug, Zap, MessageSquare } from 'lucide-react'
import Card from '../ui/Card'

export default function QuickStats({ activeCount }) {
  const stats = [
    {
      label: 'Connected Tools',
      value: activeCount,
      max: 3,
      icon: Plug,
      color: 'text-blue-400',
      bgColor: 'bg-blue-500/10',
    },
    {
      label: 'Available Agents',
      value: 3,
      icon: Zap,
      color: 'text-purple-400',
      bgColor: 'bg-purple-500/10',
    },
    {
      label: 'Chat Status',
      value: activeCount > 0 ? 'Ready' : 'Needs Setup',
      icon: MessageSquare,
      color: activeCount > 0 ? 'text-green-400' : 'text-yellow-400',
      bgColor: activeCount > 0 ? 'bg-green-500/10' : 'bg-yellow-500/10',
    },
  ]

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {stats.map(({ label, value, max, icon: Icon, color, bgColor }) => (
        <Card key={label} className="flex items-center gap-4">
          <div className={`h-10 w-10 rounded-lg ${bgColor} flex items-center justify-center`}>
            <Icon size={20} className={color} />
          </div>
          <div>
            <p className="text-xs text-slate-400">{label}</p>
            <p className="text-lg font-semibold text-white">
              {value}{max ? <span className="text-slate-500 text-sm">/{max}</span> : ''}
            </p>
          </div>
        </Card>
      ))}
    </div>
  )
}
