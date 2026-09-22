import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'

const status = {
  status: 'healthy',
  healthy: true,
  checked_at: '2026-09-22T09:00:00Z',
  heartbeat: {
    service_name: 'trading-core',
    state: 'running',
    last_seen_at: '2026-09-22T08:59:52Z',
    age_seconds: 8,
    reason: null,
  },
  queue: { pending: 2, processing: 1, failed: 0 },
}

const positions = {
  items: [
    {
      id: 10,
      exchange_config_id: 1,
      asset_config_id: 7,
      quantity: '0.12500000',
      average_entry_price: '61234.12345678',
      stored_realized_pnl: '18.42000000',
      created_at: '2026-09-21T08:00:00Z',
      updated_at: '2026-09-22T08:58:00Z',
    },
    {
      id: 11,
      exchange_config_id: 1,
      asset_config_id: 8,
      quantity: '0.00000000',
      average_entry_price: '0',
      stored_realized_pnl: '-2.12000000',
      created_at: '2026-09-21T08:00:00Z',
      updated_at: '2026-09-22T08:58:00Z',
    },
  ],
  limit: 100,
  offset: 0,
  total: 2,
}

const runs = {
  items: [
    {
      id: 31,
      asset_config_id: 7,
      strategy_name: 'momentum-v2',
      status: 'completed',
      idempotency_key: 'hidden-from-ui',
      parameters: {},
      scheduled_boundary: '2026-09-22T08:00:00Z',
      started_at: '2026-09-22T08:00:01Z',
      ended_at: '2026-09-22T08:00:03Z',
      created_at: '2026-09-22T08:00:00Z',
      updated_at: '2026-09-22T08:00:03Z',
    },
  ],
  limit: 100,
  offset: 0,
  total: 1,
}

const fills = {
  items: [
    {
      id: 41,
      order_id: 21,
      exchange_fill_id: 'fill-41',
      quantity: '0.01000000',
      price: '62345.12000000',
      fee_amount: '0.42000000',
      fee_currency: 'USDT',
      executed_at: '2026-09-22T08:00:02Z',
      created_at: '2026-09-22T08:00:02Z',
      updated_at: '2026-09-22T08:00:02Z',
    },
  ],
  limit: 100,
  offset: 0,
  total: 1,
}

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={['/']}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function mockApi(responses: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const path = String(input)
      const body = responses[path]
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: body === undefined ? 404 : 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }),
  )
}

describe('dashboard', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a loading skeleton while dashboard requests are pending', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))

    renderDashboard()

    expect(screen.getByRole('status', { name: 'Loading dashboard' })).toBeInTheDocument()
  })

  it('shows a useful error and retries failed dashboard requests', async () => {
    let failing = true
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      if (failing) return Promise.resolve(new Response('{}', { status: 503 }))
      const path = String(input)
      const body: Record<string, unknown> = {
        '/api/v1/status': status,
        '/api/v1/positions?limit=100': positions,
        '/api/v1/runs?limit=100': runs,
        '/api/v1/fills?limit=100': fills,
      }
      return Promise.resolve(new Response(JSON.stringify(body[path]), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    }))

    renderDashboard()
    expect(await screen.findByRole('alert')).toHaveTextContent('Data could not be loaded')

    failing = false
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { name: 'Operations overview' })).toBeInTheDocument()
  })

  it('shows meaningful empty states without inventing financial activity', async () => {
    const empty = { items: [], limit: 100, offset: 0, total: 0 }
    mockApi({
      '/api/v1/status': status,
      '/api/v1/positions?limit=100': empty,
      '/api/v1/runs?limit=100': empty,
      '/api/v1/fills?limit=100': empty,
    })

    renderDashboard()

    expect(await screen.findByText('No recent runs')).toBeInTheDocument()
    expect(screen.getByText('No recent fills')).toBeInTheDocument()
    expect(screen.getByTestId('persisted-realized-pnl')).toHaveTextContent('0.00')
  })

  it('shows core state, persisted financial facts, and recent activity', async () => {
    mockApi({
      '/api/v1/status': status,
      '/api/v1/positions?limit=100': positions,
      '/api/v1/runs?limit=100': runs,
      '/api/v1/fills?limit=100': fills,
    })

    renderDashboard()

    expect(await screen.findByRole('heading', { name: 'Operations overview' })).toBeInTheDocument()
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    expect(screen.getByText('8s ago')).toBeInTheDocument()
    expect(screen.getByText('2 pending')).toBeInTheDocument()
    expect(screen.getByText('1 processing')).toBeInTheDocument()
    expect(screen.getByText('0 failed')).toBeInTheDocument()

    const pnlCard = screen.getByTestId('persisted-realized-pnl')
    expect(within(pnlCard).getByText('Persisted realized PnL')).toBeInTheDocument()
    expect(within(pnlCard).getByText('16.30')).toBeInTheDocument()
    expect(screen.getByText('1 open / nonzero')).toBeInTheDocument()
    expect(screen.getByText('momentum-v2')).toBeInTheDocument()
    expect(screen.getByText(/62,345\.12/)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Recent fill price timeline' })).toBeInTheDocument()
    expect(screen.queryByText(/estimated pnl/i)).not.toBeInTheDocument()
  })
})
