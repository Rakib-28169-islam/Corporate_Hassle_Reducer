import Badge from '../ui/Badge'
import Spinner from '../ui/Spinner'

export default function ConnectionStatus({ status, isPolling = false }) {
  return (
    <div className="flex items-center gap-2">
      <Badge status={status} />
      {isPolling && <Spinner size="sm" />}
    </div>
  )
}
