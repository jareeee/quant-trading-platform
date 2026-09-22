import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'

const asset = {
  id: 7,
  exchange_config_id: 1,
  symbol: 'BTC/USDT',
  base_asset: 'BTC',
  quote_asset: 'USDT',
  enabled: true,
  settings: { timeframe: '5m' },
  created_at: '2026-09-20T00:00:00Z',
  updated_at: '2026-09-22T00:00:00Z',
}

function jsonResponse(body: object, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function renderCommands() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  return render(
    <MemoryRouter initialEntries={['/commands']}>
      <QueryClientProvider client={queryClient}><App /></QueryClientProvider>
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('commands page', () => {
  it('keeps core-wide reconciliation available when the asset list is empty', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [], limit: 100, offset: 0, total: 0 })
      }
      if (path === '/api/v1/core/commands/reconcile' && init?.method === 'POST') {
        return jsonResponse({ id: 12, status: 'pending', type: 'reconcile', requested_at: '2026-09-22T10:00:00Z', outcome: 'created' }, 202)
      }
      if (path === '/api/v1/commands/12') {
        return jsonResponse({ id: 12, status: 'completed', type: 'reconcile', requested_at: '2026-09-22T10:00:00Z', processed_at: '2026-09-22T10:00:01Z', error: null })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderCommands()

    expect(await screen.findByText('No assets configured')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reconcile all assets' }))
    expect(await screen.findByText('Completed successfully')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/core/commands/reconcile', expect.objectContaining({
      body: '{"asset_id":null}',
      method: 'POST',
    }))
  })

  it('locks every submit control while the same command intent is in flight', async () => {
    const user = userEvent.setup()
    let resolvePost!: (response: Response) => void
    const pendingPost = new Promise<Response>((resolve) => { resolvePost = resolve })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [asset], limit: 100, offset: 0, total: 1 })
      }
      if (init?.method === 'POST') return pendingPost
      if (path === '/api/v1/commands/99') {
        return jsonResponse({ id: 99, status: 'completed', type: 'pause', requested_at: '2026-09-22T10:00:00Z', processed_at: '2026-09-22T10:00:01Z', error: null })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderCommands()
    await screen.findByRole('heading', { name: 'Durable commands' })

    const pause = screen.getByRole('button', { name: 'Pause BTC/USDT' })
    await user.dblClick(pause)
    await waitFor(() => expect(pause).toBeDisabled())
    expect(screen.getByRole('status')).toHaveTextContent('Command in progress. Submit controls are locked.')
    expect(screen.getByRole('button', { name: 'Resume BTC/USDT' })).toBeDisabled()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)

    resolvePost(jsonResponse({ id: 99, status: 'pending', type: 'pause', requested_at: '2026-09-22T10:00:00Z', outcome: 'created' }, 202))
    await screen.findByText('Completed successfully')
    expect(pause).toBeEnabled()
  })

  it('sanitizes enqueue and terminal failures and retries as a fresh intent', async () => {
    const user = userEvent.setup()
    let postCount = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [asset], limit: 100, offset: 0, total: 1 })
      }
      if (path === '/api/v1/assets/7/commands/pause' && init?.method === 'POST') {
        postCount += 1
        if (postCount === 1) throw new Error('token=raw-secret sqlite=/private.db')
        return jsonResponse({ id: 88, status: 'pending', type: 'pause', requested_at: '2026-09-22T10:00:00Z', outcome: 'created' }, 202)
      }
      if (path === '/api/v1/commands/88') {
        return jsonResponse({ id: 88, status: 'failed', type: 'pause', requested_at: '2026-09-22T10:00:00Z', processed_at: '2026-09-22T10:00:01Z', error: 'token=another-secret' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderCommands()
    await screen.findByRole('heading', { name: 'Durable commands' })

    await user.click(screen.getByRole('button', { name: 'Pause BTC/USDT' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Command was not accepted')
    expect(document.body).not.toHaveTextContent('raw-secret')
    expect(document.body).not.toHaveTextContent('/private.db')

    await user.click(screen.getByRole('button', { name: 'Retry command submission' }))
    expect(await screen.findByText('Command failed')).toBeInTheDocument()
    expect(screen.getByText('Command processing failed. Review core health before retrying.')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('another-secret')

    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
    const keys = posts.map(([, init]) => (init?.headers as Record<string, string>)['Idempotency-Key'])
    expect(posts).toHaveLength(2)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('requires exact typed close confirmation before posting the destructive command', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [asset], limit: 100, offset: 0, total: 1 })
      }
      if (path === '/api/v1/assets/7/commands/close' && init?.method === 'POST') {
        return jsonResponse({ id: 70, status: 'pending', type: 'close', requested_at: '2026-09-22T10:00:00Z', outcome: 'created' }, 202)
      }
      if (path === '/api/v1/commands/70') {
        return jsonResponse({ id: 70, status: 'completed', type: 'close', requested_at: '2026-09-22T10:00:00Z', processed_at: '2026-09-22T10:00:01Z', error: null })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderCommands()
    await screen.findByRole('heading', { name: 'Durable commands' })

    await user.click(screen.getByRole('button', { name: 'Close position for BTC/USDT' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Confirm close position' })
    expect(dialog).toHaveTextContent('CLOSE 7')
    const submit = screen.getByRole('button', { name: 'Submit close command' })
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText('Type CLOSE 7 to confirm'), 'close 7')
    expect(submit).toBeDisabled()
    await user.clear(screen.getByLabelText('Type CLOSE 7 to confirm'))
    await user.type(screen.getByLabelText('Type CLOSE 7 to confirm'), 'CLOSE 7')
    expect(submit).toBeEnabled()
    await user.click(submit)

    await screen.findByText('Completed successfully')
    const closeRequest = fetchMock.mock.calls.find(([path]) => path === '/api/v1/assets/7/commands/close')
    expect(closeRequest?.[1]).toMatchObject({
      body: '{"confirmation":"CLOSE 7"}',
      headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
      method: 'POST',
    })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('uses exact resume and reconcile contracts with a fresh key for every intent', async () => {
    const user = userEvent.setup()
    let nextCommandId = 50
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [asset], limit: 100, offset: 0, total: 1 })
      }
      if (init?.method === 'POST') {
        const type = path.endsWith('/resume') ? 'resume' : 'reconcile'
        return jsonResponse({ id: nextCommandId++, status: 'pending', type, requested_at: '2026-09-22T10:00:00Z', outcome: 'created' }, 202)
      }
      if (path.startsWith('/api/v1/commands/')) {
        const id = Number(path.split('/').at(-1))
        return jsonResponse({ id, status: 'completed', type: 'command', requested_at: '2026-09-22T10:00:00Z', processed_at: '2026-09-22T10:00:01Z', error: null })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderCommands()
    await screen.findByRole('heading', { name: 'Durable commands' })

    for (const name of ['Resume BTC/USDT', 'Reconcile BTC/USDT', 'Reconcile all assets']) {
      await user.click(screen.getByRole('button', { name }))
      await waitFor(() => expect(screen.getByText('Completed successfully')).toBeInTheDocument())
    }

    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts.map(([path, init]) => [path, init?.body])).toEqual([
      ['/api/v1/assets/7/commands/resume', '{}'],
      ['/api/v1/core/commands/reconcile', '{"asset_id":7}'],
      ['/api/v1/core/commands/reconcile', '{"asset_id":null}'],
    ])
    const keys = posts.map(([, init]) => (init?.headers as Record<string, string>)['Idempotency-Key'])
    expect(new Set(keys).size).toBe(3)
    expect(keys.every((key) => typeof key === 'string' && key.length > 0)).toBe(true)
  })

  it('stops automatic polling after a status error and retries only the status check', async () => {
    const user = userEvent.setup()
    let statusAttempts = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [asset], limit: 100, offset: 0, total: 1 })
      }
      if (path === '/api/v1/assets/7/commands/pause' && init?.method === 'POST') {
        return jsonResponse({ id: 91, status: 'pending', type: 'pause', requested_at: '2026-09-22T10:00:00Z', outcome: 'created' }, 202)
      }
      if (path === '/api/v1/commands/91') {
        statusAttempts += 1
        if (statusAttempts === 1) return jsonResponse({ detail: 'token=do-not-render' }, 503)
        return jsonResponse({ id: 91, status: 'completed', type: 'pause', requested_at: '2026-09-22T10:00:00Z', processed_at: '2026-09-22T10:00:01Z', error: null })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderCommands()
    await screen.findByRole('heading', { name: 'Durable commands' })

    await user.click(screen.getByRole('button', { name: 'Pause BTC/USDT' }))
    expect(await screen.findByText('Command status could not be loaded.')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent('do-not-render')
    expect(screen.getByRole('button', { name: 'Pause BTC/USDT' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Retry command status' }))

    expect(await screen.findByText('Completed successfully')).toBeInTheDocument()
    expect(statusAttempts).toBe(2)
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  })

  it('enqueues pause with the exact contract then polls pending through processing to success', async () => {
    const user = userEvent.setup()
    const statuses = ['pending', 'processing', 'completed']
    let pollIndex = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path === '/api/v1/assets?limit=100') {
        return jsonResponse({ items: [asset], limit: 100, offset: 0, total: 1 })
      }
      if (path === '/api/v1/assets/7/commands/pause' && init?.method === 'POST') {
        return jsonResponse({
          id: 41,
          status: 'pending',
          type: 'pause',
          requested_at: '2026-09-22T10:00:00Z',
          outcome: 'created',
        }, 202)
      }
      if (path === '/api/v1/commands/41') {
        const status = statuses[Math.min(pollIndex++, statuses.length - 1)]
        return jsonResponse({
          id: 41,
          status,
          type: 'pause',
          requested_at: '2026-09-22T10:00:00Z',
          processed_at: status === 'succeeded' ? '2026-09-22T10:00:02Z' : null,
          error: null,
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderCommands()

    expect(await screen.findByRole('heading', { name: 'Durable commands' })).toBeInTheDocument()
    expect(screen.getByText('BTC/USDT')).toBeInTheDocument()
    expect(screen.getByText('Enabled for trading')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Pause BTC/USDT' }))

    expect(await screen.findByText('Command accepted')).toBeInTheDocument()
    expect(await screen.findByText('Completed successfully', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(pollIndex).toBe(3)

    const pauseRequest = fetchMock.mock.calls.find(([path]) => path === '/api/v1/assets/7/commands/pause')
    expect(pauseRequest?.[1]).toMatchObject({
      body: '{}',
      headers: expect.objectContaining({ 'Idempotency-Key': expect.any(String) }),
      method: 'POST',
    })
  })
})
