import { describe, expect, it } from 'vitest'

import {
  categoryLabel, formatDuration, formatTimestamp, formatWhen, outcomeLabel, parseTimestamp, toneOf10, toneOf100,
} from './format'

describe('durations and timestamps', () => {
  it('formats call length', () => {
    expect(formatDuration(1121)).toBe('18:41')
    expect(formatDuration(5)).toBe('00:05')
    expect(formatDuration(3725)).toBe('1:02:05')
    expect(formatDuration(null)).toBe('—')
  })

  it('formats transcript timestamps with minutes past 59, like the scorer', () => {
    expect(formatTimestamp(2.9)).toBe('00:02')
    expect(formatTimestamp(3700)).toBe('61:40')
  })

  it('parses scorer timestamps', () => {
    expect(parseTimestamp('04:12')).toBe(252)
    expect(parseTimestamp('61:40')).toBe(3700)
    expect(parseTimestamp('1:02:05')).toBe(3725)
    expect(parseTimestamp('soon')).toBeNull()
    expect(parseTimestamp('4')).toBeNull()
  })
})

describe('formatWhen', () => {
  const now = new Date(2026, 9, 21, 15, 0)
  it('uses Today / Yesterday / a date', () => {
    expect(formatWhen(new Date(2026, 9, 21, 13, 30).toISOString(), now)).toBe('Today, 1:30 PM')
    expect(formatWhen(new Date(2026, 9, 20, 16, 48).toISOString(), now)).toBe('Yesterday, 4:48 PM')
    expect(formatWhen(new Date(2026, 9, 17, 9, 5).toISOString(), now)).toBe('Oct 17, 9:05 AM')
    expect(formatWhen(new Date(2025, 9, 17, 9, 5).toISOString(), now)).toBe('Oct 17, 2025, 9:05 AM')
    expect(formatWhen(new Date(2026, 9, 21, 12, 44).toISOString(), now, ' ')).toBe('Today 12:44 PM')
  })
})

describe('labels and tones', () => {
  it('labels categories and outcomes', () => {
    expect(categoryLabel('objection_handling')).toBe('Objection handling')
    expect(outcomeLabel('no_sale')).toBe('No sale')
    expect(outcomeLabel('not_interested')).toBe('Not interested')
  })

  it('colours category scores out of 10', () => {
    expect([7, 6, 5, 4].map(toneOf10)).toEqual(['green', 'amber', 'amber', 'red'])
  })

  it('colours overall scores out of 100', () => {
    expect([75, 74, 60, 59].map(toneOf100)).toEqual(['green', 'amber', 'amber', 'red'])
  })
})
