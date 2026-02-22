import { useState, useEffect, useRef, useCallback } from 'react'
import { WS_URL } from '../utils/constants'

export default function useWebSocket(userId = 'default') {
  const [messages, setMessages] = useState([])
  const [isConnected, setIsConnected] = useState(false)
  const [isThinking, setIsThinking] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(`${WS_URL}/ws/${userId}`)

    ws.onopen = () => {
      setIsConnected(true)
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)

        if (data.type === 'thinking') {
          setIsThinking(true)
          return
        }

        setIsThinking(false)

        if (data.type === 'system') {
          setMessages(prev => [...prev, {
            id: Date.now(),
            type: 'system',
            content: data.message,
            timestamp: new Date(),
          }])
        } else if (data.type === 'response') {
          // Extract human-readable text from structured response
          let displayContent = data.combined_answer
          if (!displayContent) {
            const raw = data.data
            if (raw && typeof raw === 'object') {
              displayContent = raw.final_answer || raw.error || JSON.stringify(raw, null, 2)
            } else {
              displayContent = raw
            }
          }

          setMessages(prev => [...prev, {
            id: Date.now(),
            type: 'assistant',
            content: displayContent,
            route: data.route,
            agent: data.agent,
            routedBy: data.routed_by,
            timestamp: new Date(),
          }])
        } else if (data.type === 'error') {
          setMessages(prev => [...prev, {
            id: Date.now(),
            type: 'error',
            content: data.message,
            timestamp: new Date(),
          }])
        }
      } catch {
        // Non-JSON message
        setMessages(prev => [...prev, {
          id: Date.now(),
          type: 'assistant',
          content: event.data,
          timestamp: new Date(),
        }])
      }
    }

    ws.onclose = () => {
      setIsConnected(false)
      setIsThinking(false)
      // Auto-reconnect after 3s
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      ws.close()
    }

    wsRef.current = ws
  }, [userId])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendMessage = useCallback((message) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return

    // Add user message to local state
    setMessages(prev => [...prev, {
      id: Date.now(),
      type: 'user',
      content: message,
      timestamp: new Date(),
    }])

    wsRef.current.send(JSON.stringify({ message }))
  }, [])

  const clearMessages = useCallback(() => {
    setMessages([])
  }, [])

  return {
    messages,
    isConnected,
    isThinking,
    sendMessage,
    clearMessages,
  }
}
