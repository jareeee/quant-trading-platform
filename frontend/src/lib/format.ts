export function formatDate(value: string | null): string {
  if (!value) return 'Not available'
  return new Intl.DateTimeFormat('en', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(new Date(value))
}

export function formatDecimal(value: string, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits,
    minimumFractionDigits: 2,
  }).format(Number(value))
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
