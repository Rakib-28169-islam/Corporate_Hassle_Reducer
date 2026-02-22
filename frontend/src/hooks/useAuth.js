import { useState, useEffect, useCallback } from 'react'
import { getAllStatus, connectTool, disconnectTool } from '../api/auth'
import { getUserId } from '../utils/userId'

export default function useAuth() {
  const [connections, setConnections] = useState({
    gmail: { id: null, status: 'NOT_CONNECTED', toolkit: 'gmail' },
    slack: { id: null, status: 'NOT_CONNECTED', toolkit: 'slack' },
    outlook: { id: null, status: 'NOT_CONNECTED', toolkit: 'outlook' },
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchStatus = useCallback(async () => {
    const uid = getUserId()
    if (!uid) {
      setLoading(false)
      return
    }
    try {
      const data = await getAllStatus(uid)
      setConnections(data)
      setError(null)
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to fetch status')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  const connect = useCallback(async (toolName) => {
    try {
      const data = await connectTool(toolName, getUserId())
      // Update local state with initiated connection
      setConnections(prev => ({
        ...prev,
        [toolName]: { id: data.connection_id, status: 'INITIATED', toolkit: toolName },
      }))
      return data
    } catch (err) {
      setError(err.response?.data?.detail || `Failed to connect ${toolName}`)
      throw err
    }
  }, [])

  const disconnect = useCallback(async (toolName) => {
    const conn = connections[toolName]
    if (!conn?.id) return

    try {
      await disconnectTool(conn.id)
      setConnections(prev => ({
        ...prev,
        [toolName]: { id: null, status: 'NOT_CONNECTED', toolkit: toolName },
      }))
    } catch (err) {
      setError(err.response?.data?.detail || `Failed to disconnect ${toolName}`)
      throw err
    }
  }, [connections])

  const updateConnection = useCallback((toolName, data) => {
    setConnections(prev => ({
      ...prev,
      [toolName]: data,
    }))
  }, [])

  const activeCount = Object.values(connections).filter(c => c.status === 'ACTIVE').length

  return {
    connections,
    loading,
    error,
    connect,
    disconnect,
    fetchStatus,
    updateConnection,
    activeCount,
  }
}
