import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, ApiResponseError, apiClient } from './client'

interface HealthResponse {
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

    const result = await apiClient.get<HealthResponse>('/health')

    expect(result).toEqual({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health', {
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

    const request = apiClient.get<HealthResponse>('/health')

    await expect(request).rejects.toEqual(
      expect.objectContaining<ApiError>({
        message: 'API request failed with status 503',
        name: 'ApiError',
        status: 503,
      }),
    )
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

    const request = apiClient.get<HealthResponse>('/health')

    await expect(request).rejects.toEqual(
      expect.objectContaining<ApiResponseError>({
        message: 'API response was not valid JSON',
        name: 'ApiResponseError',
      }),
    )
  })
})
