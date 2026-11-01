import { Link, useParams } from 'react-router-dom'

// Rebuilt in the next commit (section G).
export function CallDetailPage() {
  const { id = '' } = useParams()
  return (
    <div className="flex flex-col gap-6">
      <Link to="/">All calls</Link>
      <p className="font-mono">{id}</p>
    </div>
  )
}
