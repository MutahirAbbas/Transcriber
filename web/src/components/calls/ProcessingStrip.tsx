import { isActive, useInProgress, useSettings } from '../../api/hooks'
import type { CallSummary } from '../../api/types'
import { formatDuration } from '../../lib/format'
import { smallCard } from '../../lib/ui'
import { Bar } from '../Badges'

// remote: queued means no transcriber has claimed it yet (the Mac may be offline)
export function stageLabel(c: CallSummary, remote: boolean): string {
  switch (c.status) {
    case 'queued':
      return remote ? 'Waiting for transcriber' : 'Queued'
    case 'transcribing':
      return `Transcribing · ${Math.round(c.progress * 100)}%`
    case 'scoring':
      return 'Scoring with AI…'
    case 'error':
      return `Error: ${c.error ?? 'unknown error'}`
    case 'done':
      return 'Done'
  }
}

export function ProcessingStrip() {
  const { data } = useInProgress()
  const remote = useSettings().data?.worker_mode === 'remote'
  if (!data?.length) return null
  const queued = data.filter(isActive).length

  return (
    <section aria-label="In progress" className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2.5">
        <h2 className="m-0 text-base font-semibold">Processing</h2>
        {queued > 0 && <span className="font-mono text-sm text-muted">{queued} in queue</span>}
      </div>
      <ul className="m-0 p-0 list-none grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {data.map((c) => {
          const failed = c.status === 'error'
          const pct = Math.round(c.progress * 100)
          return (
            <li key={c.id} className={`${smallCard} px-4 py-3.5 flex flex-col gap-2.5 min-w-0`}>
              <div className="flex justify-between gap-2 items-baseline">
                <span className="font-mono text-md truncate" title={c.filename}>
                  {c.filename}
                </span>
                <span className="text-sm text-muted shrink-0">{formatDuration(c.duration_s)}</span>
              </div>
              <div role="progressbar" aria-label={`${c.filename} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
                <Bar value={failed ? 1 : c.progress} className={failed ? 'bg-red' : 'bg-accent'} />
              </div>
              <div className={`text-sm break-words ${failed ? 'text-red' : 'text-nav'}`}>{stageLabel(c, remote)}</div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
