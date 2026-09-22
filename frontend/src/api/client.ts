const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number) {
    super(`API request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
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
    const response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: 'application/json' },
      method: 'GET',
    })

    if (!response.ok) {
      throw new ApiError(response.status)
    }

    try {
      return (await response.json()) as T
    } catch (error: unknown) {
      throw new ApiResponseError(error)
    }
  },
}
