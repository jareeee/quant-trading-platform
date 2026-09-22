import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'

const page = <T,>(items: T[]) => ({ items, limit: 100, offset: 0, total: items.length })

function renderDetail(path = '/assets/7') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={client}><App /></QueryClientProvider></MemoryRouter>)
}

describe('asset detail page', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders a not-found state for an absent numeric asset', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/api/v1/assets/404') return Promise.resolve(new Response(JSON.stringify({ detail: 'asset not found' }), { status: 404, headers: { 'Content-Type': 'application/json' } }))
      return Promise.resolve(new Response(JSON.stringify(page([])), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))

    renderDetail('/assets/404')

    expect(await screen.findByRole('heading', { name: 'Asset not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to assets' })).toHaveAttribute('href', '/assets')
  })

  it('fetches the numeric asset and filters persisted activity to that asset', async () => {
    const responses: Record<string, unknown> = {
      '/api/v1/assets/7': { id: 7, exchange_config_id: 1, symbol: 'BTC/USDT', base_asset: 'BTC', quote_asset: 'USDT', enabled: true, settings: { timeframe: '5m', leverage: 2 }, created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-22T00:00:00Z' },
      '/api/v1/positions?limit=100': page([
        { id: 1, exchange_config_id: 1, asset_config_id: 7, quantity: '0.12000000', average_entry_price: '61000.00000000', stored_realized_pnl: '4.50000000', created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-22T00:00:00Z' },
        { id: 2, exchange_config_id: 1, asset_config_id: 8, quantity: '9', average_entry_price: '1', stored_realized_pnl: '999', created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-22T00:00:00Z' },
      ]),
      '/api/v1/runs?limit=100': page([
        { id: 31, asset_config_id: 7, strategy_name: 'btc-momentum', status: 'completed', idempotency_key: 'secretless-key', parameters: {}, scheduled_boundary: '2026-09-22T08:00:00Z', started_at: '2026-09-22T08:00:01Z', ended_at: '2026-09-22T08:00:03Z', created_at: '2026-09-22T08:00:00Z', updated_at: '2026-09-22T08:00:03Z' },
        { id: 32, asset_config_id: 8, strategy_name: 'eth-only', status: 'failed', idempotency_key: 'other', parameters: {}, scheduled_boundary: '2026-09-22T08:00:00Z', started_at: '2026-09-22T08:00:01Z', ended_at: null, created_at: '2026-09-22T08:00:00Z', updated_at: '2026-09-22T08:00:03Z' },
      ]),
      '/api/v1/orders?limit=100': page([
        { id: 51, exchange_config_id: 1, strategy_run_id: 31, signal_id: 4, client_order_id: 'btc-order', exchange_order_id: 'exchange-51', symbol: 'BTC/USDT', side: 'buy', order_type: 'market', status: 'filled', quantity: '0.01', price: null, filled_quantity: '0.01', submitted_at: '2026-09-22T08:00:02Z', created_at: '2026-09-22T08:00:02Z', updated_at: '2026-09-22T08:00:02Z' },
        { id: 52, exchange_config_id: 1, strategy_run_id: 32, signal_id: 5, client_order_id: 'eth-order', exchange_order_id: null, symbol: 'ETH/USDT', side: 'sell', order_type: 'market', status: 'failed', quantity: '1', price: null, filled_quantity: '0', submitted_at: null, created_at: '2026-09-22T08:00:02Z', updated_at: '2026-09-22T08:00:02Z' },
      ]),
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => Promise.resolve(new Response(JSON.stringify(responses[String(input)]), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    vi.stubGlobal('fetch', fetchMock)

    renderDetail()

    expect(await screen.findByRole('heading', { name: 'BTC/USDT' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/assets/7', expect.anything())
    expect(screen.getByText('timeframe')).toBeInTheDocument()
    expect(screen.getByText('5m')).toBeInTheDocument()
    expect(screen.getByText('0.12000000')).toBeInTheDocument()
    expect(screen.getByText('Persisted realized PnL')).toBeInTheDocument()
    expect(screen.getByText('4.50000000')).toBeInTheDocument()
    expect(screen.getByText('btc-momentum')).toBeInTheDocument()
    expect(screen.getByText('btc-order')).toBeInTheDocument()
    expect(screen.queryByText('eth-only')).not.toBeInTheDocument()
    expect(screen.queryByText('eth-order')).not.toBeInTheDocument()
    expect(screen.queryByText(/estimated pnl/i)).not.toBeInTheDocument()
  })
})
