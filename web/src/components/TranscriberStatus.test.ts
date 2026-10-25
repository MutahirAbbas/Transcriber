import { describe, expect, it } from 'vitest'

import { workerLine } from './TranscriberStatus'

const now = new Date('2026-10-26T12:00:00Z')

describe('workerLine', () => {
  it('names the worker when online', () => {
    expect(workerLine({ online: true, last_seen: now.toISOString(), worker_id: "Mutahir's Mac", model: null }, now))
      .toBe("Mutahir's Mac · online")
  })

  it('says when it was last seen when offline', () => {
    const twoHours = new Date(now.getTime() - 2 * 3600 * 1000).toISOString()
    expect(workerLine({ online: false, last_seen: twoHours, worker_id: "Mutahir's Mac", model: null }, now))
      .toBe('offline · last seen 2 h ago')
    expect(workerLine({ online: false, last_seen: null, worker_id: null, model: null }, now))
      .toBe('offline · never connected')
  })
})
