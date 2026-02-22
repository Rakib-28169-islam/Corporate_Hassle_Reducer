/**
 * Persistent user identity based on email address.
 * Stored in localStorage so it persists across sessions.
 * All API calls, WebSocket, and Composio entity_id use this.
 */

const STORAGE_KEY = 'chr_user_email'

export function getUserId() {
  return localStorage.getItem(STORAGE_KEY)
}

export function setUserId(email) {
  localStorage.setItem(STORAGE_KEY, email)
}

export function clearUserId() {
  localStorage.removeItem(STORAGE_KEY)
}
