export function formatDate(value: string | null): string {
  if (!value) return 'Not available'
  return new Intl.DateTimeFormat('en', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(new Date(value))
}

export function formatDecimal(value: string, maximumFractionDigits = 2): string {
  if (!Number.isInteger(maximumFractionDigits) || maximumFractionDigits < 2) return '—'

  const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(value)
  if (!match) return '—'

  const [, sign, integerDigits = '0', fractionDigits = ''] = match
  const scale = 10n ** BigInt(maximumFractionDigits)
  const paddedFraction = fractionDigits.padEnd(maximumFractionDigits + 1, '0')
  const keptFraction = paddedFraction.slice(0, maximumFractionDigits)
  const shouldRoundUp = (paddedFraction.at(maximumFractionDigits) ?? '0') >= '5'
  let scaled = BigInt(integerDigits) * scale + BigInt(keptFraction || '0')
  if (shouldRoundUp) scaled += 1n

  const whole = scaled / scale
  let fraction = (scaled % scale).toString().padStart(maximumFractionDigits, '0')
  while (fraction.length > 2 && fraction.endsWith('0')) fraction = fraction.slice(0, -1)

  const prefix = sign === '-' && scaled !== 0n ? '-' : ''
  return `${prefix}${whole.toLocaleString('en-US')}.${fraction}`
}

export function isNonzeroDecimal(value: string): boolean {
  return !/^[-+]?0*(?:\.0*)?$/.test(value.trim())
}

export function sumDecimalStrings(values: string[]): string {
  const places = Math.max(0, ...values.map((value) => value.split('.')[1]?.length ?? 0))
  const scale = BigInt(10) ** BigInt(places)
  const total = values.reduce((sum, value) => {
    const negative = value.startsWith('-')
    const unsigned = value.replace(/^[-+]/, '')
    const [whole = '0', fraction = ''] = unsigned.split('.')
    const scaled = BigInt(whole || '0') * scale + BigInt(fraction.padEnd(places, '0') || '0')
    return sum + (negative ? -scaled : scaled)
  }, BigInt(0))
  const negative = total < 0
  const absolute = negative ? -total : total
  const whole = absolute / scale
  const fraction = places ? `.${(absolute % scale).toString().padStart(places, '0')}` : ''
  return `${negative ? '-' : ''}${whole}${fraction}`
}
