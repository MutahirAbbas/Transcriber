// Turns the stored segments and findings into what the page shows. Pure, so it can be tested.
import type { CardPhase, Finding, PhaseMark, Segment } from '../api/types'
import { formatTimestamp, parseTimestamp } from './format'

export interface Turn {
  start: number
  end: number
  speaker: string | null
  name: string | null
  text: string
  customer: boolean
  flag: boolean
  coach: boolean
}

export interface PhaseSpan {
  phase: string
  label: string
  category: string
  start: number
  end: number
}

export type Row = { kind: 'divider'; span: PhaseSpan } | { kind: 'turn'; turn: Turn; index: number }

export const findingTime = (f: Pick<Finding, 'timestamp'>) => parseTimestamp(f.timestamp) ?? 0

/** One span per phase occurrence, covering the whole call: from each start to the next. */
export function phaseSpans(phases: PhaseMark[] | null, card: CardPhase[], duration: number): PhaseSpan[] {
  const known = new Map(card.map((p) => [p.id, p]))
  const marks = (phases ?? []).flatMap((m) => {
    const def = known.get(m.phase)
    const start = parseTimestamp(m.start)
    return def && start != null ? [{ def, start }] : []
  })
  return marks
    .map(({ def, start }, i) => ({
      phase: def.id,
      label: def.label,
      category: def.category,
      start: i === 0 ? 0 : start,
      end: Math.max(start, i + 1 < marks.length ? marks[i + 1]!.start : duration),
    }))
    .filter((s) => s.end > s.start)
}

/** Consecutive segments from one speaker read as one turn, but never across a phase boundary,
 *  and segments with nobody on them stay separate rather than merging into one wall of text. */
export function buildTurns(
  segments: Segment[],
  speakers: Record<string, string>,
  agentSpeaker: string | null,
  findings: Finding[] | null,
  spans: PhaseSpan[],
): Turn[] {
  const boundaries = new Set(spans.map((s) => s.start))
  const turns: Turn[] = []
  for (const seg of segments) {
    const last = turns[turns.length - 1]
    const crosses = [...boundaries].some((b) => last && b > last.start && b <= seg.start)
    if (last && seg.speaker && last.speaker === seg.speaker && !crosses) {
      last.text = `${last.text} ${seg.text.trim()}`
      last.end = seg.end
    } else {
      turns.push({
        start: seg.start,
        end: seg.end,
        speaker: seg.speaker,
        name: seg.speaker ? (speakers[seg.speaker] ?? null) : null,
        text: seg.text.trim(),
        customer: !!(seg.speaker && agentSpeaker && seg.speaker !== agentSpeaker),
        flag: false,
        coach: false,
      })
    }
  }
  for (const f of findings ?? []) {
    if (f.kind === 'good') continue
    const turn = turns[turnAt(turns, findingTime(f))]
    if (turn) turn[f.kind] = true
  }
  return turns
}

/** Index of the turn playing at `t`: the last one to start by then. */
export function turnAt(turns: Pick<Turn, 'start'>[], t: number): number {
  let lo = 0
  let hi = turns.length - 1
  let found = turns.length ? 0 : -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (turns[mid]!.start <= t) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return found
}

export function buildRows(turns: Turn[], spans: PhaseSpan[]): Row[] {
  const rows: Row[] = []
  let next = 0
  turns.forEach((turn, index) => {
    while (next < spans.length && spans[next]!.start <= turn.start) {
      rows.push({ kind: 'divider', span: spans[next]! })
      next += 1
    }
    rows.push({ kind: 'turn', turn, index })
  })
  return rows
}

export const spanRange = (s: PhaseSpan) => `${formatTimestamp(s.start)} – ${formatTimestamp(s.end)}`

/** Literal, case-insensitive matches of a typed phrase. */
export function matchRanges(text: string, query: string): [number, number][] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const hay = text.toLowerCase()
  const out: [number, number][] = []
  for (let i = hay.indexOf(q); i !== -1; i = hay.indexOf(q, i + q.length)) out.push([i, i + q.length])
  return out
}

/** Light to deep as the score drops; 7 and up stays neutral. */
export function phaseFill(score: number | null): string {
  if (score == null || score >= 7) return 'bg-phase-neutral text-ink'
  if (score >= 5) return 'bg-phase-faint text-rust-ink'
  if (score >= 3) return 'bg-phase-light text-rust-ink'
  return 'bg-phase-deep text-rust-ink'
}

/** Waveform bars: 400 bins is more than fit, so take the peak of each group. */
export function downsample(peaks: number[], bars: number): number[] {
  if (peaks.length <= bars) return peaks
  return Array.from({ length: bars }, (_, i) => {
    const from = Math.floor((i * peaks.length) / bars)
    const to = Math.floor(((i + 1) * peaks.length) / bars)
    return Math.max(...peaks.slice(from, to))
  })
}
