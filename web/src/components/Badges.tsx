import { clamp01, outcomeLabel, toneBg, toneOf100 } from '../lib/format'
import { WarnIcon } from './Icons'

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="text-muted">—</span>
  const sale = outcome === 'sale'
  return (
    <span
      className={`inline-block px-2.5 py-[3px] rounded-full text-sm font-medium whitespace-nowrap ${
        sale ? 'bg-sale-bg text-sale-text' : 'bg-badge-bg text-badge-text'
      }`}
    >
      {outcomeLabel(outcome)}
    </span>
  )
}

export function FlagsChip({ count }: { count: number }) {
  if (!count) return <span className="text-muted">—</span>
  return (
    <span className="inline-flex items-center gap-1.5 text-red font-medium text-md whitespace-nowrap">
      <WarnIcon />
      {count} {count === 1 ? 'flag' : 'flags'}
    </span>
  )
}

/** Thin progress-style bar. `value` is 0–1. */
export function Bar({ value, className, width = 'w-full' }: { value: number; className: string; width?: string }) {
  return (
    <div className={`${width} h-1.5 rounded-bar bg-border-subtle overflow-hidden`}>
      <div className={`h-full ${className}`} style={{ width: `${clamp01(value) * 100}%` }} />
    </div>
  )
}

export function ScoreCell({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="font-semibold w-7">{score}</span>
      <Bar value={score / 100} className={toneBg[toneOf100(score)]} width="w-[90px]" />
    </div>
  )
}
