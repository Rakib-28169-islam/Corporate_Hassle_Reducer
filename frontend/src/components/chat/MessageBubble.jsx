import { Bot, User, AlertCircle, Info } from 'lucide-react'

function formatContent(content) {
  if (content === null || content === undefined) return 'No response'
  if (typeof content === 'string') return content
  // For agent responses that return objects
  try {
    return JSON.stringify(content, null, 2)
  } catch {
    return String(content)
  }
}

export default function MessageBubble({ message }) {
  const { type, content, route, agent, routedBy } = message

  if (type === 'system') {
    return (
      <div className="flex justify-center my-2">
        <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 rounded-full text-xs text-slate-400">
          <Info size={12} />
          {formatContent(content)}
        </div>
      </div>
    )
  }

  if (type === 'error') {
    return (
      <div className="flex justify-center my-2">
        <div className="flex items-center gap-2 px-3 py-1.5 bg-red-500/10 border border-red-500/30 rounded-lg text-xs text-red-400">
          <AlertCircle size={12} />
          {formatContent(content)}
        </div>
      </div>
    )
  }

  const isUser = type === 'user'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''} my-3`}>
      {/* Avatar */}
      <div className={`h-8 w-8 rounded-full flex items-center justify-center shrink-0 ${
        isUser
          ? 'bg-blue-600/20 border border-blue-500/30'
          : 'bg-purple-600/20 border border-purple-500/30'
      }`}>
        {isUser ? <User size={14} className="text-blue-400" /> : <Bot size={14} className="text-purple-400" />}
      </div>

      {/* Bubble */}
      <div className={`max-w-[75%] ${isUser ? 'text-right' : ''}`}>
        <div className={`rounded-2xl px-4 py-2.5 text-sm ${
          isUser
            ? 'bg-blue-600 text-white rounded-br-md'
            : 'bg-slate-800 text-slate-200 border border-slate-700 rounded-bl-md'
        }`}>
          {typeof content === 'object' && content !== null ? (
            <pre className="whitespace-pre-wrap text-xs font-mono">{formatContent(content)}</pre>
          ) : (
            <p className="whitespace-pre-wrap">{formatContent(content)}</p>
          )}
        </div>

        {/* Metadata for assistant messages */}
        {!isUser && route && (
          <div className="flex items-center gap-2 mt-1.5 text-[10px] text-slate-500">
            <span>Route: {route}</span>
            <span>Agent: {agent}</span>
            <span>Via: {routedBy}</span>
          </div>
        )}
      </div>
    </div>
  )
}
