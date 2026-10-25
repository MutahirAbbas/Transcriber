import { describe, expect, it } from 'vitest'

import type { CallSummary } from '../../api/types'
import { stageLabel } from './ProcessingStrip'

const call = (status: CallSummary['status'], extra: Partial<CallSummary> = {}) =>
  ({ status, progress: 0.7, error: null, ...extra }) as CallSummary

describe('stageLabel', () => {
  it('says who a queued call is waiting for', () => {
    expect(stageLabel(call('queued'), false)).toBe('Queued')
    expect(stageLabel(call('queued'), true)).toBe('Waiting for transcriber')
  })

  it('labels the other stages the same in both modes', () => {
    for (const remote of [false, true]) {
      expect(stageLabel(call('transcribing'), remote)).toBe('Transcribing · 70%')
      expect(stageLabel(call('scoring'), remote)).toBe('Scoring with AI…')
      expect(stageLabel(call('error', { error: 'boom' }), remote)).toBe('Error: boom')
    }
  })
})
