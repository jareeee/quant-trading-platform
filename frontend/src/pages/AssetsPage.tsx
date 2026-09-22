import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { Asset } from '../api/domain'
import { useAssets } from '../api/queries'
import { EmptyState, ErrorPanel, LoadingPanel } from '../components/States'
import { formatDate } from '../lib/format'

type SortOption = 'symbol-asc' | 'symbol-desc' | 'updated-desc' | 'id-asc'

function settingsSummary(settings: Record<string, unknown>): string[] {
  return Object.entries(settings)
    .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value))
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${String(value)}`)
}

function compareAssets(sort: SortOption) {
  return (a: Asset, b: Asset) => {
    if (sort === 'symbol-asc') return a.symbol.localeCompare(b.symbol)
    if (sort === 'symbol-desc') return b.symbol.localeCompare(a.symbol)
    if (sort === 'updated-desc') return b.updated_at.localeCompare(a.updated_at)
    return a.id - b.id
  }
}

export function AssetsPage() {
  const query = useAssets()
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortOption>('symbol-asc')
  const assets = useMemo(() => {
    const term = search.trim().toLowerCase()
    return [...(query.data?.items ?? [])]
      .filter((asset) => !term || asset.symbol.toLowerCase().includes(term) || asset.base_asset.toLowerCase().includes(term) || asset.quote_asset.toLowerCase().includes(term))
      .sort(compareAssets(sort))
  }, [query.data, search, sort])

  if (query.isPending) return <LoadingPanel label="Loading assets" />
  if (query.isError) return <ErrorPanel onRetry={() => void query.refetch()} />

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm font-semibold text-[#9d7cff]">CONFIGURATION</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-white">Asset configurations</h1>
        <p className="mt-2 text-sm text-slate-400">Read-only strategy universe and non-secret runtime settings.</p>
      </header>

      <section aria-label="Asset controls" className="flex flex-col gap-3 rounded-xl border border-[#253047] bg-[#111722] p-4 sm:flex-row sm:items-end sm:justify-between">
        <label className="block flex-1 text-xs font-medium uppercase tracking-wide text-slate-500">Search assets
          <input aria-label="Search assets" className="mt-2 w-full rounded-lg border border-[#303a50] bg-[#0b1019] px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-600 focus:border-[#7132f5]" onChange={(event) => setSearch(event.target.value)} placeholder="Symbol or currency" type="search" value={search} />
        </label>
        <label className="block text-xs font-medium uppercase tracking-wide text-slate-500">Sort
          <select aria-label="Sort assets" className="mt-2 w-full rounded-lg border border-[#303a50] bg-[#0b1019] px-3 py-2.5 text-sm text-white outline-none focus:border-[#7132f5] sm:w-48" onChange={(event) => setSort(event.target.value as SortOption)} value={sort}>
            <option value="symbol-asc">Symbol A–Z</option><option value="symbol-desc">Symbol Z–A</option><option value="updated-desc">Recently updated</option><option value="id-asc">Numeric ID</option>
          </select>
        </label>
      </section>

      <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-500">
        <p>Showing {query.data.items.length} recent records of {query.data.total} total</p>
        {query.data.total > query.data.items.length && <p>Search and sort apply to the fetched subset.</p>}
      </div>

      {assets.length === 0 ? <EmptyState detail={search ? 'Try a different symbol or currency.' : 'Configured assets will appear here.'} title={search ? 'No matching assets' : 'No assets configured'} /> : (
        <section className="overflow-hidden rounded-xl border border-[#253047] bg-[#111722]" aria-label="Assets list">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="border-b border-[#253047] bg-[#0e141f] text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3 font-medium">Asset</th><th className="px-5 py-3 font-medium">State</th><th className="px-5 py-3 font-medium">Exchange</th><th className="px-5 py-3 font-medium">Settings</th><th className="px-5 py-3 text-right font-medium">Updated</th></tr></thead>
              <tbody>{assets.map((asset) => <tr className="border-b border-[#1d2637] last:border-0 hover:bg-[#141b29]" key={asset.id}>
                <td className="px-5 py-4"><Link aria-label={`${asset.symbol} asset details`} className="font-semibold text-white hover:text-[#a98cff]" to={`/assets/${asset.id}`}>{asset.symbol}</Link><span className="mt-1 block text-xs text-slate-500">ID #{asset.id}</span></td>
                <td className={`px-5 py-4 font-medium ${asset.enabled ? 'text-emerald-400' : 'text-slate-500'}`}>{asset.enabled ? 'Enabled' : 'Disabled'}</td>
                <td className="px-5 py-4 tabular-nums text-slate-400">#{asset.exchange_config_id}</td>
                <td className="max-w-[280px] px-5 py-4 text-xs text-slate-400">{settingsSummary(asset.settings).length ? settingsSummary(asset.settings).join(' · ') : 'No settings'}</td>
                <td className="px-5 py-4 text-right text-xs text-slate-400">{formatDate(asset.updated_at)}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
