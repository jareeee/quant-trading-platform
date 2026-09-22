import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from './App'

function renderApp(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider></MemoryRouter>)
}

describe('application shell', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders semantic primary navigation on the command page', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], limit: 100, offset: 0, total: 0 }), { status: 200 })))
    renderApp('/commands')

    expect(await screen.findByRole('heading', { name: 'Durable commands' })).toBeInTheDocument()
    const navigation = screen.getByRole('navigation', { name: 'Primary navigation' })
    expect(navigation).toContainElement(screen.getByRole('link', { name: 'Dashboard' }))
    expect(navigation).toContainElement(screen.getByRole('link', { name: 'Assets' }))
    expect(navigation).toContainElement(screen.getByRole('link', { name: 'Commands' }))
  })

  it('renders an accessible not-found route', () => {
    renderApp('/missing')
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Return to dashboard' })).toHaveAttribute('href', '/')
  })
})
