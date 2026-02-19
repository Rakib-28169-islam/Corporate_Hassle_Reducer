import { useOutletContext, Link } from 'react-router-dom'
import { ArrowRight, Wrench, MessageSquare } from 'lucide-react'
import QuickStats from '../components/dashboard/QuickStats'
import ToolStatusCard from '../components/dashboard/ToolStatusCard'
import Card from '../components/ui/Card'
import Spinner from '../components/ui/Spinner'

export default function DashboardPage() {
  const { connections, loading, activeCount } = useOutletContext()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spinner size="lg" />
      </div>
    )
  }

  return (
    <div className="space-y-8">
      {/* Quick Stats */}
      <QuickStats activeCount={activeCount} />

      {/* Tool Status */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-semibold">Tool Status</h3>
          <Link to="/tools" className="flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300 transition-colors">
            Manage <ArrowRight size={14} />
          </Link>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {Object.entries(connections).map(([name, conn]) => (
            <ToolStatusCard key={name} toolName={name} connection={conn} />
          ))}
        </div>
      </div>

      {/* Quick Actions */}
      <div>
        <h3 className="text-white font-semibold mb-4">Quick Actions</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Link to="/tools">
            <Card className="hover:border-blue-500/30 transition-all cursor-pointer group">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-lg bg-blue-500/10 flex items-center justify-center">
                  <Wrench size={20} className="text-blue-400" />
                </div>
                <div>
                  <p className="text-white font-medium group-hover:text-blue-400 transition-colors">Connect Tools</p>
                  <p className="text-xs text-slate-400">Link your Gmail, Slack, or Outlook</p>
                </div>
                <ArrowRight size={16} className="text-slate-500 ml-auto group-hover:text-blue-400 transition-colors" />
              </div>
            </Card>
          </Link>

          <Link to="/chat">
            <Card className="hover:border-purple-500/30 transition-all cursor-pointer group">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-lg bg-purple-500/10 flex items-center justify-center">
                  <MessageSquare size={20} className="text-purple-400" />
                </div>
                <div>
                  <p className="text-white font-medium group-hover:text-purple-400 transition-colors">Start Chatting</p>
                  <p className="text-xs text-slate-400">Ask your AI assistant anything</p>
                </div>
                <ArrowRight size={16} className="text-slate-500 ml-auto group-hover:text-purple-400 transition-colors" />
              </div>
            </Card>
          </Link>
        </div>
      </div>
    </div>
  )
}
