import { Link, useParams } from 'react-router-dom'

import { BackIcon } from '../components/Icons'

// Built in the next stage.
export function CallDetailPage() {
  const { id = '' } = useParams()
  return (
    <div className="flex flex-col gap-6">
      <Link to="/" className="inline-flex items-center gap-1.5 text-muted text-md min-h-8">
        <BackIcon />
        All calls
      </Link>
      <p className="m-0 font-mono text-md">{id}</p>
    </div>
  )
}
