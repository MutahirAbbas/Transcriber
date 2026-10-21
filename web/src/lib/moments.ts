import type { CoachableMoment, Score, Segment } from '../api/types'
import { categoryLabel, parseTimestamp } from './format'

export interface PlacedMoment {
  moment: CoachableMoment
  category: string | null // scorecard key, when what_happened names one
}

/** The scorecard category `text` mentions first ("objection handling" or "objection_handling"). */
export function categoryFor(text: string, categories: string[]): string | null {
  const lower = text.toLowerCase()
  let best: { cat: string; at: number } | null = null
  for (const cat of categories) {
    const hits = [lower.indexOf(categoryLabel(cat).toLowerCase()), lower.indexOf(cat.toLowerCase())].filter((i) => i >= 0)
    const at = hits.length ? Math.min(...hits) : -1
    if (at >= 0 && (!best || at < best.at)) best = { cat, at }
  }
  return best?.cat ?? null
}

/** Index of the last segment starting at or before `t` (segments sorted by start); -1 if none. */
export function segmentAt(segments: Segment[], t: number): number {
  let lo = 0
  let hi = segments.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (segments[mid]!.start <= t) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return found
}

/** Attach each coachable moment to the turn it happened in; the rest go to "Other moments". */
export function placeMoments(
  segments: Segment[],
  moments: CoachableMoment[],
  categories: string[],
): { bySegment: Map<number, PlacedMoment[]>; other: PlacedMoment[] } {
  const bySegment = new Map<number, PlacedMoment[]>()
  const other: PlacedMoment[] = []
  for (const moment of moments) {
    const placed = { moment, category: categoryFor(moment.what_happened, categories) }
    const t = parseTimestamp(moment.timestamp)
    const idx = t == null ? -1 : segmentAt(segments, t)
    if (idx < 0) other.push(placed)
    else bySegment.set(idx, [...(bySegment.get(idx) ?? []), placed])
  }
  return { bySegment, other }
}

export interface PhraseCheck {
  phrase: string
  present: boolean
  at: number | null // start of the first segment containing it
}

/** One line per required phrase. The scorer's `missing` list decides; the transcript gives the timestamp. */
export function checkPhrases(phrases: string[], segments: Segment[], score: Score | null): PhraseCheck[] {
  const missing = (score?.compliance?.missing ?? []).map((m) => m.toLowerCase())
  return phrases.map((phrase) => {
    const p = phrase.toLowerCase()
    const seg = segments.find((s) => s.text.toLowerCase().includes(p))
    const flagged = missing.some((m) => m.includes(p))
    return { phrase, present: !flagged && !!seg, at: seg && !flagged ? seg.start : null }
  })
}

/** Speaker label for a turn: mapped after scoring, raw before, "Speaker" when unlabelled. */
export function speakerLabel(speaker: string | null, map: Record<string, string>): string {
  if (!speaker) return 'Speaker'
  return map[speaker] ?? speaker
}
