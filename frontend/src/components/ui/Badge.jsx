import { STATUS_COLORS } from '../../utils/constants'

export default function Badge({ status }) {
  const style = STATUS_COLORS[status] || STATUS_COLORS.NOT_CONNECTED

  return (
    <span
      className={`
        inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium
        ${style.bg} ${style.text} border ${style.border}
      `}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${
        status === 'ACTIVE' ? 'bg-green-400 animate-pulse' :
        status === 'INITIATED' ? 'bg-yellow-400 animate-pulse' :
        'bg-current'
      }`} />
      {style.label}
    </span>
  )
}
