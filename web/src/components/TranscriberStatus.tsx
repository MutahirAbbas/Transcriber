import { useSettings, useWorkerStatus } from '../api/hooks'
import type { WorkerStatus } from '../api/types'
import { formatAgo, transcriberLabel } from '../lib/format'

export function workerLine(status: WorkerStatus, now: Date = new Date()): string {
  if (status.online) return `${status.worker_id ?? 'Transcriber'} · online`
  return status.last_seen ? `offline · last seen ${formatAgo(status.last_seen, now)}` : 'offline · never connected'
}

/** Sidebar footer. Local mode: the model. Remote mode: whether the Mac transcriber is connected. */
export function TranscriberStatus() {
  const { data: settings } = useSettings()
  const remote = settings?.worker_mode === 'remote'
  const { data: status } = useWorkerStatus(remote)

  return (
    <div className="basis-full md:basis-auto px-2.5 pt-3 md:py-3 border-t border-border flex flex-col gap-1">
      <span className="text-xs text-muted uppercase tracking-label">Transcriber</span>
      {remote ? (
        <>
          <span className="flex items-center gap-2 text-sm text-ink" aria-live="polite">
            <span aria-hidden="true" className={`w-2 h-2 rounded-full shrink-0 ${status?.online ? 'bg-green' : 'bg-idle'}`} />
            <span className="truncate">{status ? workerLine(status) : '…'}</span>
          </span>
          {status?.model && <span className="font-mono text-sm text-muted truncate">{status.model}</span>}
        </>
      ) : (
        <span className="font-mono text-sm text-ink">
          {settings ? transcriberLabel(settings.provider, settings.model) : '…'}
        </span>
      )}
    </div>
  )
}
