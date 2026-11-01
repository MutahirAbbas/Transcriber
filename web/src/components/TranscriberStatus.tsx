import { useSettings, useWorkerStatus } from '../api/hooks'
import type { WorkerStatus } from '../api/types'
import { formatAgo, modelShort } from '../lib/format'

export function workerLine(status: WorkerStatus, now: Date = new Date()): string {
  if (status.online) return `${status.worker_id ?? 'Transcriber'} · online`
  return status.last_seen ? `offline · last seen ${formatAgo(status.last_seen, now)}` : 'offline · never connected'
}

const heading = 'text-xs font-medium text-muted uppercase tracking-head'

/** Sidebar footer: what transcribes (in remote mode, whether the Mac is connected) and what scores. */
export function TranscriberStatus() {
  const { data: settings } = useSettings()
  const remote = settings?.worker_mode === 'remote'
  const { data: status } = useWorkerStatus(remote)
  const online = remote ? !!status?.online : true
  const model = remote ? status?.model : settings?.model

  return (
    <div className="basis-full md:basis-auto pt-3 md:pt-4 border-t border-border flex flex-wrap md:flex-col gap-x-6 gap-y-3">
      <div className="flex flex-col gap-1 min-w-0">
        <span className={heading}>Transcriber</span>
        <span className="flex items-center gap-2 text-md text-ink" aria-live="polite">
          <span aria-hidden="true" className={`w-2 h-2 rounded-full shrink-0 ${online ? 'bg-link' : 'bg-wave'}`} />
          <span className="truncate">{remote ? (status ? workerLine(status) : '…') : 'This Mac · local'}</span>
        </span>
        {model && <span className="font-mono text-sm text-muted truncate">{model}</span>}
      </div>
      <div className="flex flex-col gap-1 min-w-0">
        <span className={heading}>Scoring</span>
        <span className="text-md text-ink truncate">{settings ? `Claude · ${modelShort(settings.scorer_model)}` : '…'}</span>
      </div>
    </div>
  )
}
