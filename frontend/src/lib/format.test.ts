import { describe, expect, it } from 'vitest'

import { formatDecimal } from './format'

describe('formatDecimal', () => {
  it('formats and rounds decimal strings without losing precision', () => {
    expect(formatDecimal('9007199254740993.125', 2)).toBe('9,007,199,254,740,993.13')
  })

  it('rejects values that are not plain decimal strings', () => {
    expect(formatDecimal('not-a-decimal')).toBe('—')
  })
})
