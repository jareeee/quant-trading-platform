const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number) {
    super(`API request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
  }
}

export class ApiNetworkError extends Error {
  constructor() {
    super('API network request failed')
    this.name = 'ApiNetworkError'
  }
}

export class ApiResponseError extends Error {
  constructor(cause: unknown) {
    super('API response was not valid JSON', { cause })
    this.name = 'ApiResponseError'
  }
}

export const apiClient = {
  async get<T>(path: `/${string}`): Promise<T> {
    const response = await safeFetch(`${API_BASE}${path}`, {
      headers: { Accept: 'application/json' },
      method: 'GET',
    })

    return parseResponse<T>(response)
  },

  async post<T>(
    path: `/${string}`,
    body: object,
    { idempotencyKey }: { idempotencyKey: string },
  ): Promise<T> {
    const response = await safeFetch(`${API_BASE}${path}`, {
      body: JSON.stringify(body),
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
      },
      method: 'POST',
    })

    return parseResponse<T>(response)
  },
}

async function safeFetch(input: RequestInfo | URL, init: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch {
    throw new ApiNetworkError()
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new ApiError(response.status)
  }

  try {
    return (await response.json()) as T
  } catch (error: unknown) {
    throw new ApiResponseError(error)
  }
}
