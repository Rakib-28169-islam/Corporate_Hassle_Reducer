import client from './client'

export async function syncOnConnected(toolName, userId = 'default', connectionId = null) {
  const params = { tool_name: toolName, user_id: userId }
  if (connectionId) params.connection_id = connectionId
  const { data } = await client.post('/api/sync/on-connected', null, { params })
  return data
}

export async function getSyncStatus(userId = 'default') {
  const { data } = await client.get(`/api/sync/status/${userId}`)
  return data
}

export async function forceRefresh(toolName, userId = 'default') {
  const { data } = await client.post(`/api/sync/refresh/${toolName}`, null, {
    params: { user_id: userId },
  })
  return data
}
