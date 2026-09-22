import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { useAsset, useOrders, usePositions, useRuns } from '../api/queries'
import { EmptyState, ErrorPanel, LoadingPanel } from '../components/States'
import { formatDate } from '../lib/format'

function NotFound() {
  return <section className="rounded-xl border border-[#253047] bg-[#111722] p-8"><p className="text-sm font-semibold text-[#9d7cff]">404</p><h1 className="mt-2 text-2xl font-semibold text-white">Asset not found</h1><p className="mt-2 text-sm text-slate-400">No asset configuration exists for this numeric identifier.</p><Link className="mt-5 inline-block text-sm font-semibold text-[#a98cff] hover:text-white" to="/assets">Back to assets</Link></section>
}

function ActivityTable({ children, headings }: { children: React.ReactNode; headings: string[] }) {
  return <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm"><thead className="border-b border-[#253047] text-xs uppercase tracking-wide text-slate-500"><tr>{headings.map((heading) => <th className="px-5 py-3 font-medium" key={heading}>{heading}</th>)}</tr></thead><tbody>{children}</tbody></table></div>
}

export function AssetDetailPage() {
  const { assetId: rawAssetId } = useParams<{ assetId: string }>()
  const parsed = rawAssetId && /^\d+$/.test(rawAssetId) ? Number(rawAssetId) : null
  const assetQuery = useAsset(parsed)
  const positionsQuery = usePositions()
  const runsQuery = useRuns()
  const ordersQuery = useOrders()
  const activityQueries = [positionsQuery, runsQuery, ordersQuery]

  if (parsed === null) return <NotFound />
  if (assetQuery.isPending || activityQueries.some((query) => query.isPending)) return <LoadingPanel label="Loading asset detail" />
  if (assetQuery.error instanceof ApiError && assetQuery.error.status === 404) return <NotFound />
  if (assetQuery.isError || activityQueries.some((query) => query.isError)) return <ErrorPanel onRetry={() => void Promise.all([assetQuery.refetch(), ...activityQueries.map((query) => query.refetch())])} />

  const asset = assetQuery.data!
  const positions = positionsQuery.data!.items.filter((position) => position.asset_config_id === asset.id)
  const runs = runsQuery.data!.items.filter((run) => run.asset_config_id === asset.id).sort((a, b) => b.started_at.localeCompare(a.started_at))
  const runIds = new Set(runs.map((run) => run.id))
  const orders = ordersQuery.data!.items.filter((order) => order.symbol === asset.symbol || (order.strategy_run_id !== null && runIds.has(order.strategy_run_id))).sort((a, b) => b.created_at.localeCompare(a.created_at))
  const subsetIncomplete = [positionsQuery.data!, runsQuery.data!, ordersQuery.data!].some((page) => page.total > page.items.length)

  return (
    <div className="space-y-7">
      <Link className="text-sm font-medium text-slate-400 hover:text-[#a98cff]" to="/assets">← All assets</Link>
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div><p className="text-sm font-semibold text-[#9d7cff]">ASSET #{asset.id}</p><h1 className="mt-1 text-3xl font-semibold tracking-tight text-white">{asset.symbol}</h1><p className="mt-2 text-sm text-slate-400">Exchange configuration #{asset.exchange_config_id}</p></div>
        <div className={`text-sm font-semibold ${asset.enabled ? 'text-emerald-400' : 'text-slate-500'}`}>{asset.enabled ? 'Enabled' : 'Disabled'}</div>
      </header>

      <section className="grid gap-5 lg:grid-cols-[0.8fr_1.2fr]">
        <article className="rounded-xl border border-[#253047] bg-[#111722] p-5"><h2 className="text-lg font-semibold text-white">Configuration</h2><dl className="mt-4 grid grid-cols-2 gap-4 text-sm"><div><dt className="text-slate-500">Base asset</dt><dd className="mt-1 text-slate-200">{asset.base_asset}</dd></div><div><dt className="text-slate-500">Quote asset</dt><dd className="mt-1 text-slate-200">{asset.quote_asset}</dd></div><div><dt className="text-slate-500">Created</dt><dd className="mt-1 text-slate-200">{formatDate(asset.created_at)}</dd></div><div><dt className="text-slate-500">Updated</dt><dd className="mt-1 text-slate-200">{formatDate(asset.updated_at)}</dd></div></dl></article>
        <article className="rounded-xl border border-[#253047] bg-[#111722] p-5"><h2 className="text-lg font-semibold text-white">Non-secret settings</h2>{Object.keys(asset.settings).length === 0 ? <p className="mt-4 text-sm text-slate-500">No asset-specific settings.</p> : <dl className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2">{Object.entries(asset.settings).map(([key, value]) => <div className="flex items-start justify-between gap-4 border-b border-[#1d2637] pb-3" key={key}><dt className="font-mono text-xs text-slate-500">{key}</dt><dd className="break-all text-right font-mono text-xs text-slate-200">{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl>}</article>
      </section>

      {subsetIncomplete && <p className="rounded-lg border border-[#303a50] bg-[#0e141f] px-4 py-3 text-xs text-slate-400">Activity is filtered from the most recent 100 records per API collection; older matching records may exist.</p>}

      <section className="rounded-xl border border-[#253047] bg-[#111722]"><div className="p-5"><h2 className="text-lg font-semibold text-white">Persisted positions</h2><p className="mt-1 text-xs text-slate-500">Values are stored records; no mark-to-market calculations are shown.</p></div>{positions.length === 0 ? <div className="p-5 pt-0"><EmptyState detail="No position record was found in the recently fetched subset." title="No recent position" /></div> : <ActivityTable headings={['Quantity', 'Average entry', 'Persisted realized PnL', 'Updated']}>{positions.map((position) => <tr className="border-t border-[#1d2637]" key={position.id}><td className="px-5 py-4 font-mono text-slate-200">{position.quantity}</td><td className="px-5 py-4 font-mono text-slate-300">{position.average_entry_price}</td><td className="px-5 py-4 font-mono text-slate-200">{position.stored_realized_pnl}</td><td className="px-5 py-4 text-slate-400">{formatDate(position.updated_at)}</td></tr>)}</ActivityTable>}</section>

      <section className="grid gap-6 xl:grid-cols-2">
        <article className="overflow-hidden rounded-xl border border-[#253047] bg-[#111722]"><h2 className="p-5 text-lg font-semibold text-white">Recent strategy runs</h2>{runs.length === 0 ? <div className="p-5 pt-0"><EmptyState detail="No matching runs in the fetched subset." title="No recent runs" /></div> : <ActivityTable headings={['Strategy', 'Status', 'Started']}>{runs.map((run) => <tr className="border-t border-[#1d2637]" key={run.id}><td className="px-5 py-4 text-slate-200">{run.strategy_name}</td><td className="px-5 py-4 text-slate-300">{run.status}</td><td className="px-5 py-4 text-slate-400">{formatDate(run.started_at)}</td></tr>)}</ActivityTable>}</article>
        <article className="overflow-hidden rounded-xl border border-[#253047] bg-[#111722]"><h2 className="p-5 text-lg font-semibold text-white">Recent orders</h2>{orders.length === 0 ? <div className="p-5 pt-0"><EmptyState detail="No matching orders in the fetched subset." title="No recent orders" /></div> : <ActivityTable headings={['Client order', 'Side', 'Status', 'Quantity']}>{orders.map((order) => <tr className="border-t border-[#1d2637]" key={order.id}><td className="px-5 py-4 font-mono text-xs text-slate-200">{order.client_order_id}</td><td className="px-5 py-4 text-slate-300">{order.side}</td><td className="px-5 py-4 text-slate-300">{order.status}</td><td className="px-5 py-4 font-mono text-slate-300">{order.quantity}</td></tr>)}</ActivityTable>}</article>
      </section>
    </div>
  )
}
