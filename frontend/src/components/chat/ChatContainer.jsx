import { useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'
import TypingIndicator from './TypingIndicator'
import ChatInput from './ChatInput'

export default function ChatContainer({ messages, isConnected, isThinking, onSend }) {
  const scrollRef = useRef(null)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, isThinking])

  return (
    <div className="flex flex-col h-full bg-slate-900 rounded-xl border border-slate-700 overflow-hidden">
      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 chat-scroll">
        {messages.length === 0 && !isThinking && (
          <div className="flex flex-col items-center justify-center h-full text-slate-500">
            <p className="text-lg font-medium">No messages yet</p>
            <p className="text-sm mt-1">Try asking about your emails, Slack messages, or calendar events.</p>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {isThinking && <TypingIndicator />}
      </div>

      {/* Input */}
      <ChatInput onSend={onSend} disabled={!isConnected} />
    </div>
  )
}
