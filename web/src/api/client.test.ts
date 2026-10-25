import { describe, expect, it } from 'vitest'

import { safeNext } from './client'

describe('safeNext', () => {
  it('keeps same-site paths', () => {
    expect(safeNext('/calls/abc?tab=2')).toBe('/calls/abc?tab=2')
    expect(safeNext('/')).toBe('/')
  })

  it('refuses anything that could leave the site or loop', () => {
    for (const bad of [null, '', 'https://evil.example', '//evil.example', 'calls', '/login?next=/x']) {
      expect(safeNext(bad)).toBe('/')
    }
  })
})
