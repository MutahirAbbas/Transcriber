import { describe, expect, it } from 'vitest'

import type { CardPhase, Finding, Segment } from '../api/types'
import { buildRows, buildTurns, downsample, matchRanges, phaseFill, phaseSpans, turnAt } from './transcript'

const card: CardPhase[] = [
  { id: 'opening', label: 'Opening', category: 'opening' },
  { id: 'objections', label: 'Objections', category: 'objection_handling' },
  { id: 'close', label: 'Close', category: 'close' },
]
const seg = (start: number, text: string, speaker: string | null = 'A'): Segment => ({
  start, end: start + 1, text, speaker, confidence: null,
})
const finding = (kind: Finding['kind'], timestamp: string): Finding => ({ kind, timestamp, category: 'c', text: 't' })

describe('phaseSpans', () => {
  it('runs each phase to the next, the last to the end, and keeps repeats', () => {
    const spans = phaseSpans(
      [{ phase: 'opening', start: '00:00' }, { phase: 'objections', start: '01:00' }, { phase: 'close', start: '02:00' }, { phase: 'objections', start: '03:00' }],
      card, 240)
    expect(spans.map((s) => [s.phase, s.start, s.end])).toEqual([
      ['opening', 0, 60], ['objections', 60, 120], ['close', 120, 180], ['objections', 180, 240]])
  })

  it('stretches the first phase back to the start of the call', () => {
    expect(phaseSpans([{ phase: 'opening', start: '00:12' }], card, 60)[0]).toMatchObject({ start: 0, end: 60 })
  })

  it('ignores unknown phases, and survives none at all', () => {
    expect(phaseSpans([{ phase: 'mystery', start: '00:00' }], card, 60)).toEqual([])
    expect(phaseSpans(null, card, 60)).toEqual([])
  })

  it('drops a phase with no length', () => {
    const spans = phaseSpans([{ phase: 'opening', start: '00:00' }, { phase: 'close', start: '01:00' }], card, 60)
    expect(spans.map((s) => s.phase)).toEqual(['opening'])
  })
})

describe('buildTurns', () => {
  const names = { A: 'Josh', B: 'Gary' }

  it('merges one speaker’s segments and names both sides', () => {
    const turns = buildTurns([seg(0, 'Hi.'), seg(1, 'Thanks for calling.'), seg(2, 'Hello.', 'B')], names, 'A', null, [])
    expect(turns.map((t) => [t.name, t.text, t.customer])).toEqual([
      ['Josh', 'Hi. Thanks for calling.', false],
      ['Gary', 'Hello.', true],
    ])
  })

  it('splits a turn at a phase boundary', () => {
    const spans = phaseSpans([{ phase: 'opening', start: '00:00' }, { phase: 'close', start: '00:02' }], card, 10)
    expect(buildTurns([seg(0, 'one'), seg(1, 'two'), seg(2, 'three')], names, 'A', null, spans).map((t) => t.text))
      .toEqual(['one two', 'three'])
  })

  it('keeps unlabelled segments apart, and no one counts as the customer', () => {
    const turns = buildTurns([seg(0, 'one', null), seg(1, 'two', null)], {}, null, null, [])
    expect(turns).toHaveLength(2)
    expect(turns.every((t) => !t.customer && t.name === null)).toBe(true)
  })

  it('marks the turn each flag and coach finding falls in, but not good ones', () => {
    const findings = [finding('flag', '00:01'), finding('coach', '00:02'), finding('good', '00:00')]
    const turns = buildTurns([seg(0, 'a', 'A'), seg(1, 'b', 'B'), seg(2, 'c', 'A')], names, 'A', findings, [])
    expect(turns.map((t) => [t.flag, t.coach])).toEqual([[false, false], [true, false], [false, true]])
  })
})

describe('turnAt', () => {
  const turns = [{ start: 0 }, { start: 10 }, { start: 20 }]
  it('finds the turn playing', () => {
    expect([0, 5, 10, 19.9, 20, 500].map((t) => turnAt(turns, t))).toEqual([0, 0, 1, 1, 2, 2])
  })
  it('uses the first turn for time before it, and -1 for no turns', () => {
    expect(turnAt([{ start: 5 }], 1)).toBe(0)
    expect(turnAt([], 1)).toBe(-1)
  })
})

describe('buildRows', () => {
  it('puts a divider before the first turn of each phase', () => {
    const spans = phaseSpans([{ phase: 'opening', start: '00:00' }, { phase: 'close', start: '00:01' }], card, 3)
    const rows = buildRows(buildTurns([seg(0, 'a'), seg(1, 'b', 'B')], { A: 'J', B: 'G' }, 'A', null, spans), spans)
    expect(rows.map((r) => (r.kind === 'divider' ? r.span.label : r.turn.text))).toEqual(['Opening', 'a', 'Close', 'b'])
  })
})

describe('matchRanges', () => {
  it('finds every match, ignoring case and treating the query literally', () => {
    expect(matchRanges('Date of birth. BIRTH', 'birth')).toEqual([[8, 13], [15, 20]])
    expect(matchRanges('costs $5.00 (now)', '$5.00 (')).toEqual([[6, 13]])
    expect(matchRanges('anything', '  ')).toEqual([])
  })
})

describe('phaseFill', () => {
  it('deepens as the score drops, neutral from 7', () => {
    expect([10, 7, 6, 5, 4, 3, 2, 1].map((s) => phaseFill(s).split(' ')[0])).toEqual([
      'bg-phase-neutral', 'bg-phase-neutral', 'bg-phase-faint', 'bg-phase-faint', 'bg-phase-light', 'bg-phase-light',
      'bg-phase-deep', 'bg-phase-deep'])
  })
})

describe('downsample', () => {
  it('keeps the loudest of each group', () => {
    expect(downsample([0, 1, 0, 0.5, 0, 0], 3)).toEqual([1, 0.5, 0])
  })
  it('leaves short input alone', () => {
    expect(downsample([0.1, 0.2], 10)).toEqual([0.1, 0.2])
  })
})
