import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { App } from './App'

describe('application shell', () => {
  it('renders semantic navigation and routes between primary pages', async () => {
    const user = userEvent.setup()

    render(
      <MemoryRouter initialEntries={['/']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()

    const navigation = screen.getByRole('navigation', { name: 'Primary navigation' })
    expect(navigation).toContainElement(screen.getByRole('link', { name: 'Dashboard' }))
    expect(navigation).toContainElement(screen.getByRole('link', { name: 'Assets' }))
    expect(navigation).toContainElement(screen.getByRole('link', { name: 'Commands' }))

    await user.click(screen.getByRole('link', { name: 'Assets' }))
    expect(screen.getByRole('heading', { name: 'Assets' })).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'Commands' }))
    expect(screen.getByRole('heading', { name: 'Commands' })).toBeInTheDocument()
  })

  it('renders an asset detail placeholder from the route identifier', () => {
    render(
      <MemoryRouter initialEntries={['/assets/BTC-USDT']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Asset BTC-USDT' })).toBeInTheDocument()
  })

  it('renders an accessible not-found route', () => {
    render(
      <MemoryRouter initialEntries={['/missing']}>
        <App />
      </MemoryRouter>,
    )

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Return to dashboard' })).toHaveAttribute('href', '/')
  })
})
