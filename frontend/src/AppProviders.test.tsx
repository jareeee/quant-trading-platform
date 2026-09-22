import { render, screen } from '@testing-library/react'
import { useQuery } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { AppProviders } from './AppProviders'

function QueryConsumer() {
  const query = useQuery({
    queryKey: ['provider-test'],
    queryFn: async () => 'query ready',
  })

  return <p>{query.data ?? 'loading'}</p>
}

describe('AppProviders', () => {
  it('provides a working TanStack Query client', async () => {
    render(
      <AppProviders>
        <QueryConsumer />
      </AppProviders>,
    )

    expect(await screen.findByText('query ready')).toBeInTheDocument()
  })
})
