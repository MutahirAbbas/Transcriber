import { describe, expect, it } from 'vitest'

import { safeNext } from './client'

const ORIGIN = 'https://calls.tqc4.ai'

describe('safeNext', () => {
  it('keeps same-origin paths', () => {
    expect(safeNext('/calls/abc?tab=2', ORIGIN)).toBe('/calls/abc?tab=2')
    expect(safeNext('/calls/abc#transcript', ORIGIN)).toBe('/calls/abc#transcript')
    expect(safeNext('/', ORIGIN)).toBe('/')
  })

  it('refuses anything that could leave the site or loop back to login', () => {
    const bad = [
      null, '', 'calls', 'https://evil.example', '//evil.example', '/\\evil.example', '/\\/evil.example',
      '/\tevil', '/\r\nevil', '/login?next=/x', '/login',
    ]
    for (const next of bad) expect(safeNext(next, ORIGIN), String(next)).toBe('/')
  })

  it('leaves percent-encoded characters encoded (still a same-origin path)', () => {
    expect(safeNext('/%0d%0aevil', ORIGIN)).toBe('/%0d%0aevil')
  })

  it('normalises dot segments without escaping the origin', () => {
    expect(safeNext('/calls/../calls/x', ORIGIN)).toBe('/calls/x')
    expect(safeNext('/../../etc', ORIGIN)).toBe('/etc')
  })
})
