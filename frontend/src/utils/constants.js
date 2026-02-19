export const TOOLS = {
  gmail: {
    name: 'Gmail',
    description: 'Connect your Gmail account to read, send, and manage emails.',
    icon: 'Mail',
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/30',
    accentColor: '#ef4444',
  },
  slack: {
    name: 'Slack',
    description: 'Connect Slack to send messages, search channels, and manage notifications.',
    icon: 'Hash',
    color: 'text-purple-400',
    bgColor: 'bg-purple-500/10',
    borderColor: 'border-purple-500/30',
    accentColor: '#a855f7',
  },
  outlook: {
    name: 'Outlook',
    description: 'Connect Outlook for work email, calendar events, and meeting scheduling.',
    icon: 'Calendar',
    color: 'text-blue-400',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/30',
    accentColor: '#3b82f6',
  },
}

export const STATUS_COLORS = {
  ACTIVE: { text: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/30', label: 'Connected' },
  INITIATED: { text: 'text-yellow-400', bg: 'bg-yellow-500/10', border: 'border-yellow-500/30', label: 'Pending...' },
  EXPIRED: { text: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/30', label: 'Expired' },
  FAILED: { text: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/30', label: 'Failed' },
  NOT_CONNECTED: { text: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/30', label: 'Not Connected' },
}

export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
export const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000'
