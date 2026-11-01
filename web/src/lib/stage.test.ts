import { describe, expect, it } from 'vitest'

import { stageText, timeLeft } from './stage'

const base = { progress: 0, eta_s: null, error: null }

describe('stageText', () => {
  it('words every stage as designed', () => {
    expect(stageText({ ...base, stage: 'transcribing', progress: 0.42, eta_s: 30 })).toBe('Transcribing · 42% · about 30 s left')
    expect(stageText({ ...base, stage: 'separating' })).toBe('Separating speakers…')
    expect(stageText({ ...base, stage: 'scoring' })).toBe('Scoring with AI…')
    expect(stageText({ ...base, stage: 'waiting' })).toBe('Waiting for transcriber')
    expect(stageText({ ...base, stage: 'queued' })).toBe('Queued')
    expect(stageText({ ...base, stage: 'error', error: 'No audio' })).toBe('Error: No audio')
  })

  it('drops the time left when there is no honest figure', () => {
    expect(stageText({ ...base, stage: 'transcribing', progress: 0.9 })).toBe('Transcribing · 90%')
  })
})

describe('timeLeft', () => {
  it('rounds to something readable', () => {
    expect(timeLeft(2)).toBe('about 5 s left')
    expect(timeLeft(32)).toBe('about 30 s left')
    expect(timeLeft(150)).toBe('about 3 min left')
  })
})
