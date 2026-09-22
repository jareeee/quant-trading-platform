import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'

function renderAssets() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={['/assets']}>
      <QueryClientProvider client={queryClient}><App /></QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('assets page', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('searches and sorts recently fetched assets with numeric detail links', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [
        { id: 12, exchange_config_id: 2, symbol: 'ETH/USDT', base_asset: 'ETH', quote_asset: 'USDT', enabled: false, settings: { timeframe: '1h', risk_limit: '0.02' }, created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-21T00:00:00Z' },
        { id: 7, exchange_config_id: 1, symbol: 'BTC/USDT', base_asset: 'BTC', quote_asset: 'USDT', enabled: true, settings: { timeframe: '5m' }, created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-22T00:00:00Z' },
      ],
      limit: 100,
      offset: 0,
      total: 142,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    renderAssets()

    expect(await screen.findByRole('heading', { name: 'Asset configurations' })).toBeInTheDocument()
    expect(screen.getByText('Showing 2 recent records of 142 total')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /BTC\/USDT/ })).toHaveAttribute('href', '/assets/7')
    expect(screen.getByRole('link', { name: /ETH\/USDT/ })).toHaveAttribute('href', '/assets/12')
    expect(screen.getByText('Enabled')).toBeInTheDocument()
    expect(screen.getByText('Disabled')).toBeInTheDocument()
    expect(screen.getByText('timeframe: 5m')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Sort assets'), 'symbol-desc')
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('ETH/USDT')

    await user.type(screen.getByRole('searchbox', { name: 'Search assets' }), 'btc')
    expect(screen.getByRole('link', { name: /BTC\/USDT/ })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ETH\/USDT/ })).not.toBeInTheDocument()
  })
})
