import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, ApiNetworkError, ApiResponseError, apiClient } from './client'

interface StatusResponse {
  status: string
}

describe('apiClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('requests typed JSON from the relative API base', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await apiClient.get<StatusResponse>('/status')

    expect(result).toEqual({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/status', {
      headers: { Accept: 'application/json' },
      method: 'GET',
    })
  })

  it('throws a typed error for an unsuccessful response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'unavailable' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    const request = apiClient.get<StatusResponse>('/status')

    await expect(request).rejects.toEqual(
      expect.objectContaining<ApiError>({
        message: 'API request failed with status 503',
        name: 'ApiError',
        status: 503,
      }),
    )
  })

  it('posts JSON with a caller-supplied idempotency key', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 9, status: 'pending' }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await apiClient.post<{ id: number; status: string }>(
      '/assets/7/commands/pause',
      {},
      { idempotencyKey: 'intent-123' },
    )

    expect(result).toEqual({ id: 9, status: 'pending' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/assets/7/commands/pause', {
      body: '{}',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': 'intent-123',
      },
      method: 'POST',
    })
  })

  it('replaces raw network failures with a credential-safe typed error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('token=secret host=/private/path')))

    const request = apiClient.post('/core/commands/reconcile', { asset_id: null }, { idempotencyKey: 'network-attempt' })

    await expect(request).rejects.toEqual(
      expect.objectContaining<ApiNetworkError>({
        message: 'API network request failed',
        name: 'ApiNetworkError',
      }),
    )
    await expect(request).rejects.not.toHaveProperty('message', expect.stringContaining('secret'))
  })

  it('throws a typed response error when successful JSON is malformed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('{invalid', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    const request = apiClient.get<StatusResponse>('/status')

    await expect(request).rejects.toEqual(
      expect.objectContaining<ApiResponseError>({
        message: 'API response was not valid JSON',
        name: 'ApiResponseError',
      }),
    )
  })
})
