import { useEffect, useRef } from 'react'
import { getConnectionStatus } from '../api/auth'

export default function usePolling(connectionId, onStatusChange, interval = 3000) {
  const timerRef = useRef(null)

  useEffect(() => {
    if (!connectionId) return

    const poll = async () => {
      try {
        const data = await getConnectionStatus(connectionId)
        onStatusChange(data)

        // Stop polling once terminal state is reached
        if (data.status === 'ACTIVE' || data.status === 'FAILED') {
          clearInterval(timerRef.current)
          timerRef.current = null
        }
      } catch {
        // Silently retry on network errors
      }
    }

    timerRef.current = setInterval(poll, interval)
    // Also poll immediately
    poll()

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
      }
    }
  }, [connectionId, interval, onStatusChange])
}
