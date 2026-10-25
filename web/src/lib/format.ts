import type { Outcome } from '../api/types'

const pad = (n: number) => String(n).padStart(2, '0')

/** Call length: mm:ss, or h:mm:ss from an hour up. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`
}

/** Transcript timestamp: mm:ss with minutes past 59 — the same format the scorer cites. */
export function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`
}

/** "mm:ss" or "h:mm:ss" → seconds; null when it isn't a timestamp. */
export function parseTimestamp(ts: string): number | null {
  const parts = ts.trim().split(':')
  if (parts.length < 2 || parts.length > 3 || parts.some((p) => !/^\d+$/.test(p))) return null
  return parts.map(Number).reduce((acc, n) => acc * 60 + n, 0)
}

const time = (d: Date) => d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

/** "Today, 1:30 PM" · "Yesterday, 4:48 PM" · "Oct 17, 9:05 AM" · "Oct 17, 2025, 9:05 AM" */
export function formatWhen(iso: string, now: Date = new Date(), sep = ', '): string {
  const d = new Date(iso)
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(now) - startOf(d)) / 86_400_000)
  if (days === 0) return `Today${sep}${time(d)}`
  if (days === 1) return `Yesterday${sep}${time(d)}`
  const date = d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}),
  })
  return `${date}${sep}${time(d)}`
}

/** "just now" · "5 min ago" · "2 h ago" · "3 d ago" */
export function formatAgo(iso: string, now: Date = new Date()): string {
  const s = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86_400)} d ago`
}

export function categoryLabel(cat: string | null | undefined): string {
  if (!cat) return ''
  const words = cat.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export const OUTCOME_LABELS: Record<Outcome, string> = {
  sale: 'Sale',
  callback: 'Callback',
  no_sale: 'No sale',
  not_interested: 'Not interested',
  wrong_number: 'Wrong number',
  unclear: 'Unclear',
}

export const outcomeLabel = (o: string) => OUTCOME_LABELS[o as Outcome] ?? categoryLabel(o)

export type Tone = 'green' | 'amber' | 'red'

/** Category score out of 10: ≥ 7 green, 5–6 amber, ≤ 4 red. */
export const toneOf10 = (score: number): Tone => (score >= 7 ? 'green' : score >= 5 ? 'amber' : 'red')

/** Overall score out of 100: ≥ 75 green, 60–74 amber, < 60 red. */
export const toneOf100 = (score: number): Tone => (score >= 75 ? 'green' : score >= 60 ? 'amber' : 'red')

export const toneBg: Record<Tone, string> = { green: 'bg-green', amber: 'bg-amber', red: 'bg-red' }
export const toneStroke: Record<Tone, string> = { green: 'stroke-green', amber: 'stroke-amber', red: 'stroke-red' }

/** "whisper-large-v3-turbo · local" */
export function transcriberLabel(provider: string, model: string): string {
  const where = provider === 'whisper_local' ? 'local' : provider
  return model ? `${model} · ${where}` : where
}

export const clamp01 = (n: number) => Math.min(1, Math.max(0, n))
