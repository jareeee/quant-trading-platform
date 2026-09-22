import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { useFills, usePositions, useRuns, useStatus } from '../api/queries'
import { EmptyState, ErrorPanel, LoadingPanel } from '../components/States'
import { formatDate, formatDecimal, isNonzeroDecimal, sumDecimalStrings } from '../lib/format'

function MetricCard({ label, value, detail, testId }: { label: string; value: string; detail: string; testId?: string }) {
  return (
    <article className="rounded-xl border border-[#253047] bg-[#111722] p-5 shadow-[0_10px_25px_rgba(0,0,0,0.16)]" data-testid={testId}>
      <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">{label}</p>
      <p className="mt-3 text-2xl font-semibold tabular-nums text-white">{value}</p>
      <p className="mt-1 text-xs text-slate-500">{detail}</p>
    </article>
  )
}

function StatusBadge({ value }: { value: string }) {
  const successful = ['healthy', 'completed', 'running', 'filled'].includes(value.toLowerCase())
  const failed = ['failed', 'error', 'stale', 'missing'].includes(value.toLowerCase())
  return <span className={successful ? 'text-emerald-400' : failed ? 'text-red-400' : 'text-slate-300'}>{value}</span>
}

export function DashboardPage() {
  const statusQuery = useStatus()
  const positionsQuery = usePositions()
  const runsQuery = useRuns()
  const fillsQuery = useFills()
  const queries = [statusQuery, positionsQuery, runsQuery, fillsQuery]

  if (queries.some((query) => query.isPending)) return <LoadingPanel label="Loading dashboard" />
  if (queries.some((query) => query.isError)) {
    return <ErrorPanel onRetry={() => void Promise.all(queries.map((query) => query.refetch()))} />
  }

  const status = statusQuery.data!
  const positions = positionsQuery.data!
  const runs = [...runsQuery.data!.items].sort((a, b) => b.started_at.localeCompare(a.started_at)).slice(0, 5)
  const fills = [...fillsQuery.data!.items].sort((a, b) => b.executed_at.localeCompare(a.executed_at)).slice(0, 8)
  const pnl = sumDecimalStrings(positions.items.map((position) => position.stored_realized_pnl))
  const openCount = positions.items.filter((position) => isNonzeroDecimal(position.quantity)).length
  const chartData = [...fills].reverse().map((fill) => ({ time: formatDate(fill.executed_at), price: Number(fill.price) }))
  const heartbeatAge = status.heartbeat.age_seconds === null ? 'Unavailable' : `${Math.round(status.heartbeat.age_seconds)}s ago`
  const positionScope = positions.total > positions.items.length
    ? `${positions.items.length} fetched stored records of ${positions.total} total`
    : `${positions.items.length} stored records`

  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-semibold text-[#9d7cff]">CONTROL PLANE</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-white">Operations overview</h1>
          <p className="mt-2 text-sm text-slate-400">Read-only, persisted trading activity from the local core.</p>
        </div>
        <p className="text-xs text-slate-500">Checked {formatDate(status.checked_at)} UTC</p>
      </header>

      <section aria-labelledby="core-health-heading" className="rounded-xl border border-[#253047] bg-[#111722] p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-300" id="core-health-heading">Core health</h2>
            <p className="mt-2 text-xl font-semibold"><StatusBadge value={status.healthy ? 'Healthy' : status.status} /></p>
          </div>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm sm:grid-cols-4">
            <div><dt className="text-slate-500">Heartbeat</dt><dd className="mt-1 tabular-nums text-slate-200">{heartbeatAge}</dd></div>
            <div><dt className="text-slate-500">Queue</dt><dd className="mt-1 text-slate-200">{status.queue.pending} pending</dd></div>
            <div><dt className="text-slate-500">Workers</dt><dd className="mt-1 text-slate-200">{status.queue.processing} processing</dd></div>
            <div><dt className="text-slate-500">Failures</dt><dd className="mt-1 text-slate-200">{status.queue.failed} failed</dd></div>
          </dl>
        </div>
      </section>

      <section aria-label="Trading metrics" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <MetricCard detail={`Sum of ${positionScope}; not mark-to-market`} label="Persisted realized PnL" testId="persisted-realized-pnl" value={formatDecimal(pnl)} />
        <MetricCard detail={`From ${positionScope}`} label="Open positions" value={`${openCount} open / nonzero`} />
        <MetricCard detail="Pending, processing, and failed" label="Command queue" value={`${status.queue.pending + status.queue.processing + status.queue.failed}`} />
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.05fr_0.95fr]">
        <article className="rounded-xl border border-[#253047] bg-[#111722] p-5">
          <div className="mb-5 flex items-baseline justify-between gap-3"><h2 className="text-lg font-semibold text-white">Recent strategy runs</h2><span className="text-xs text-slate-500">Latest fetched records</span></div>
          {runs.length === 0 ? <EmptyState detail="Strategy activity will appear after the core records a run." title="No recent runs" /> : (
            <div className="overflow-x-auto"><table className="w-full min-w-[520px] text-left text-sm"><thead className="border-b border-[#253047] text-xs uppercase tracking-wide text-slate-500"><tr><th className="pb-3 font-medium">Strategy</th><th className="pb-3 font-medium">Asset</th><th className="pb-3 font-medium">Status</th><th className="pb-3 text-right font-medium">Started</th></tr></thead><tbody>{runs.map((run) => <tr className="border-b border-[#1d2637] last:border-0" key={run.id}><td className="py-4 font-medium text-slate-200">{run.strategy_name}</td><td className="py-4 tabular-nums text-slate-400">#{run.asset_config_id}</td><td className="py-4"><StatusBadge value={run.status} /></td><td className="py-4 text-right text-slate-400">{formatDate(run.started_at)}</td></tr>)}</tbody></table></div>
          )}
        </article>

        <article className="rounded-xl border border-[#253047] bg-[#111722] p-5">
          <div className="mb-5 flex items-baseline justify-between gap-3"><h2 className="text-lg font-semibold text-white">Recent fill prices</h2><span className="text-xs text-slate-500">Persisted fills only</span></div>
          {fills.length === 0 ? <EmptyState detail="Authoritative fills will appear after an order executes." title="No recent fills" /> : <>
            <div aria-label="Recent fill price timeline" className="h-48 w-full" role="img"><ResponsiveContainer height="100%" width="100%"><LineChart data={chartData} margin={{ left: 4, right: 10, top: 8, bottom: 2 }}><XAxis dataKey="time" hide /><YAxis domain={['auto', 'auto']} tick={{ fill: '#7c879b', fontSize: 11 }} width={56} /><Tooltip contentStyle={{ background: '#0d121c', border: '1px solid #2b3447', borderRadius: 8 }} /><Line dataKey="price" dot={{ r: 3, fill: '#7132f5' }} isAnimationActive={false} stroke="#8b5cf6" strokeWidth={2} type="monotone" /></LineChart></ResponsiveContainer></div>
            <ul className="mt-4 divide-y divide-[#1d2637]">{fills.slice(0, 3).map((fill) => <li className="flex justify-between py-3 text-sm" key={fill.id}><span className="text-slate-400">Order #{fill.order_id}</span><span className="tabular-nums text-slate-200">{formatDecimal(fill.price)} × {formatDecimal(fill.quantity, 8)}</span></li>)}</ul>
          </>}
        </article>
      </section>
    </div>
  )
}
