import { describe, expect, it } from 'vitest'

import type { CallDetail } from '../api/types'
import { feedbackText, scrub } from './feedback'

const people = {
  customer: { first_name: 'Gary', last_name: 'Jones', age: 75, state: 'Maryland' },
  sale: { coverage_usd: 11000, premium_monthly_usd: 113, carrier: 'Aflac', plan_type: 'level' },
}

describe('scrub', () => {
  it.each([
    ['Gary Jones said no.', 'the customer said no.'],
    ['Then Mr. Jones paused.', 'Then the customer paused.'],
    ['Reflect Gary’s own words.', 'Reflect the customer’s own words.'],
    ['GARY hung up.', 'the customer hung up.'],
    ['He lives in Maryland.', 'He lives in their state.'],
    ['A grieving 75-year-old diabetic.', 'A grieving diabetic.'],
    ['A seventy-five year old man.', 'A man.'],
    ['Customer is 75 now.', 'Customer is [age] now.'],
    ['Customer, aged 75, agreed.', 'Customer, agreed.'],
    ['Sold $11,000 of cover.', 'Sold [amount] of cover.'],
    ['At $113/mo.', 'At [amount].'],
    ['At $113 per month.', 'At [amount].'],
    ['About $50k.', 'About [amount].'],
    ['Quoted 11,000 dollars.', 'Quoted [amount].'],
    ['Quoted eleven thousand dollars today.', 'Quoted [amount] today.'],
    ['A policy of 11000 was sold, 113 monthly.', 'A policy of [amount] was sold, [amount] monthly.'],
  ])('%s', (input, expected) => {
    expect(scrub(input, people)).toBe(expected)
  })

  it('leaves ordinary coaching text and timestamps alone', () => {
    const text = 'Cut personal stories and politics (33:33–35:48). Confirm the carrier first.'
    expect(scrub(text, people)).toBe(text)
  })

  it('does not touch a word that merely contains a name', () => {
    expect(scrub('Garyville is not Gary.', people)).toBe('Garyville is not the customer.')
  })

  it('still catches amounts and ages when customer and sale are null', () => {
    expect(scrub('A 75-year-old paid $113/mo.', { customer: null, sale: null })).toBe('A paid [amount].')
  })
})

const call = {
  ...people,
  agent: { id: 1, name: 'Josh' },
  suggested_agent: null,
  uploaded_at: '2026-10-17T12:00:00Z',
  overall_score: 36,
  id: '20261017-014852_2022625450-all-5ibfcv',
  score: {
    summary: 'Gary Jones, a 75-year-old in Maryland, bought $11,000 at $113/mo.',
    work_on_next: { intro: 'Gary needed care.', items: ['Ask Mr. Jones about budget.', 'Say the carrier, Aflac, first.'] },
  },
} as unknown as CallDetail

describe('feedbackText', () => {
  const text = feedbackText(call)

  it('has the agent, date, score, summary and work on next', () => {
    expect(text).toContain('Feedback for Josh')
    expect(text).toContain('Oct 17, 2026')
    expect(text).toContain('Score 36 / 100')
    expect(text).toContain('Work on next')
    expect(text).toContain('1. Ask the customer about budget.')
    expect(text).toContain('2. Say the carrier, Aflac, first.')
  })

  it('has no customer name, age or state, no sale amount, and no phone number from the call id', () => {
    for (const secret of ['Gary', 'Jones', '75', 'Maryland', '11,000', '11000', '113', '$', '2022625450']) {
      expect(text, secret).not.toContain(secret)
    }
  })

  it('works before a name is known', () => {
    const anon = feedbackText({ ...call, agent: null, suggested_agent: null, customer: null, sale: null })
    expect(anon.split('\n')[0]).toBe('Call feedback')
  })
})
