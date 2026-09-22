import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { App } from './App'

function renderApp(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><QueryClientProvider client={new QueryClient()}><App /></QueryClientProvider></MemoryRouter>)
}

describe('application shell', () => {
  it('renders semantic primary navigation in the command placeholder', () => {
    renderApp('/commands')

    expect(screen.getByRole('heading', { name: 'Commands' })).toBeInTheDocument()
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
