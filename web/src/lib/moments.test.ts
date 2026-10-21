import { describe, expect, it } from 'vitest'

import type { Score, Segment } from '../api/types'
import { categoryFor, checkPhrases, placeMoments, segmentAt, speakerLabel } from './moments'

const CATS = ['opening', 'discovery', 'value_presentation', 'objection_handling', 'compliance', 'close']
const seg = (start: number, text = '', speaker: string | null = null): Segment => ({
  start, end: start + 2, text, speaker, confidence: null,
})
const SEGMENTS = [seg(2, 'Hi, recorded line'), seg(14), seg(245), seg(252), seg(580)]

describe('categoryFor', () => {
  it('finds the first category named in the text', () => {
    expect(categoryFor('Weak objection handling: price was skipped before the close', CATS)).toBe('objection_handling')
    expect(categoryFor('close was rushed; discovery thin', CATS)).toBe('close')
    expect(categoryFor('value_presentation lacked detail', CATS)).toBe('value_presentation')
    expect(categoryFor('Agent talked over the customer', CATS)).toBeNull()
  })
})

describe('segmentAt / placeMoments', () => {
  it('picks the last segment starting at or before t', () => {
    expect(segmentAt(SEGMENTS, 252)).toBe(3)
    expect(segmentAt(SEGMENTS, 251.9)).toBe(2)
    expect(segmentAt(SEGMENTS, 9999)).toBe(4)
    expect(segmentAt(SEGMENTS, 1)).toBe(-1)
  })

  it('attaches moments to turns and keeps the rest aside', () => {
    const m = (timestamp: string, what = 'x') => ({ timestamp, what_happened: what, better_approach: 'y' })
    const { bySegment, other } = placeMoments(
      SEGMENTS,
      [m('04:12', 'objection handling skipped'), m('04:13'), m('00:01'), m('later')],
      CATS,
    )
    expect([...bySegment.keys()]).toEqual([3])
    expect(bySegment.get(3)!.map((p) => p.category)).toEqual(['objection_handling', null])
    expect(other.map((p) => p.moment.timestamp)).toEqual(['00:01', 'later'])
  })
})

describe('checkPhrases', () => {
  const score = (missing: string[]) => ({ compliance: { missing, red_flags: [], required_phrases_present: !missing.length } }) as unknown as Score

  it('ticks phrases found in the transcript with their timestamp', () => {
    expect(checkPhrases(['recorded', 'licensed'], SEGMENTS, score([]))).toEqual([
      { phrase: 'recorded', present: true, at: 2 },
      { phrase: 'licensed', present: false, at: null },
    ])
  })

  it("trusts the scorer's missing list", () => {
    expect(checkPhrases(['recorded'], SEGMENTS, score(['Recorded line disclosure']))[0]).toEqual({
      phrase: 'recorded', present: false, at: null,
    })
  })
})

describe('speakerLabel', () => {
  it('maps, falls back to the raw label, or says Speaker', () => {
    const map = { SPEAKER_0: 'Agent', SPEAKER_1: 'Customer' }
    expect(speakerLabel('SPEAKER_1', map)).toBe('Customer')
    expect(speakerLabel('SPEAKER_1', {})).toBe('SPEAKER_1')
    expect(speakerLabel(null, map)).toBe('Speaker')
  })
})
