import { clamp01, outcomeLabel } from '../lib/format'
import { WarnIcon } from './Icons'

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="text-muted">—</span>
  const sale = outcome === 'sale'
  return (
    <span
      className={`inline-block px-2.5 py-1 rounded-full text-md whitespace-nowrap ${
        sale ? 'bg-link-tint text-link-ink' : 'bg-pill text-muted'
      }`}
    >
      {outcomeLabel(outcome)}
    </span>
  )
}

export function FlagsChip({ count }: { count: number }) {
  if (!count) return <span className="text-muted">—</span>
  return (
    <span className="inline-flex items-center gap-1.5 text-rust text-md whitespace-nowrap" aria-label={`${count} ${count === 1 ? 'flag' : 'flags'}`}>
      <WarnIcon />
      {count}
    </span>
  )
}

/** Thin progress-style bar. `value` is 0–1. */
export function Bar({ value, className, width = 'w-full' }: { value: number; className: string; width?: string }) {
  return (
    <div className={`${width} h-1 rounded-bar bg-divider overflow-hidden`}>
      <div className={`h-full rounded-bar ${className}`} style={{ width: `${clamp01(value) * 100}%` }} />
    </div>
  )
}

export function ScoreCell({ score, target }: { score: number; target: number }) {
  return (
    <div className="flex items-center gap-3">
      <span className="font-medium w-6 tabular-nums">{score}</span>
      <Bar value={score / 100} className={score < target ? 'bg-rust' : 'bg-ink'} width="w-24" />
    </div>
  )
}
