import client from './client'

export async function connectTool(toolName, userId = 'default') {
  const { data } = await client.post(`/api/auth/connect/${toolName}`, null, {
    params: { user_id: userId },
  })
  return data
}

export async function getAllStatus(userId = 'default') {
  const { data } = await client.get('/api/auth/status', {
    params: { user_id: userId },
  })
  return data
}

export async function getConnectionStatus(connectionId) {
  const { data } = await client.get(`/api/auth/status/${connectionId}`)
  return data
}

export async function disconnectTool(connectionId) {
  const { data } = await client.post(`/api/auth/disconnect/${connectionId}`)
  return data
}
