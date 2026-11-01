import type { CallSummary } from '../api/types'

export const isInProgress = (c: Pick<CallSummary, 'status'>) => c.status !== 'done' && c.status !== 'error'

export function timeLeft(seconds: number): string {
  if (seconds < 60) return `about ${Math.max(5, Math.round(seconds / 5) * 5)} s left`
  return `about ${Math.round(seconds / 60)} min left`
}

/** The line in a call's in-progress row. */
export function stageText(c: Pick<CallSummary, 'stage' | 'progress' | 'eta_s' | 'error'>): string {
  switch (c.stage) {
    case 'queued':
      return 'Queued'
    case 'waiting':
      return 'Waiting for transcriber'
    case 'transcribing': {
      const pct = `Transcribing · ${Math.round(c.progress * 100)}%`
      return c.eta_s != null ? `${pct} · ${timeLeft(c.eta_s)}` : pct
    }
    case 'separating':
      return 'Separating speakers…'
    case 'scoring':
      return 'Scoring with AI…'
    case 'error':
      return `Error: ${c.error ?? 'unknown error'}`
    case 'done':
      return 'Done'
  }
}
