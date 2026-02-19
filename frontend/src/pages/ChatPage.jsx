import { useOutletContext } from 'react-router-dom'
import ChatContainer from '../components/chat/ChatContainer'
import useWebSocket from '../hooks/useWebSocket'
import { AlertCircle } from 'lucide-react'

export default function ChatPage() {
  const { activeCount } = useOutletContext()
  const { messages, isConnected, isThinking, sendMessage } = useWebSocket('default')

  return (
    <div className="h-[calc(100vh-8rem-2rem)]">
      {activeCount === 0 && (
        <div className="flex items-center gap-2 mb-4 px-4 py-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-sm text-yellow-400">
          <AlertCircle size={16} />
          No tools connected. Connect at least one tool for the assistant to be useful.
        </div>
      )}

      <ChatContainer
        messages={messages}
        isConnected={isConnected}
        isThinking={isThinking}
        onSend={sendMessage}
      />
    </div>
  )
}
