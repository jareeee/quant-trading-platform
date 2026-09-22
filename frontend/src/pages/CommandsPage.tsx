import { useMutation, useQuery } from '@tanstack/react-query'
import { useRef, useState } from 'react'

import { apiClient } from '../api/client'
import type { Asset, CommandEnqueueResponse, CommandStatusResponse } from '../api/domain'
import { useAssets } from '../api/queries'
import { EmptyState, ErrorPanel, LoadingPanel } from '../components/States'

type CommandIntent =
  | { kind: 'pause' | 'resume'; asset: Asset }
  | { kind: 'reconcile'; asset: Asset | null }
  | { kind: 'close'; asset: Asset; confirmation: string }

const POLL_INTERVAL_MS = 1_000
const MAX_POLL_ATTEMPTS = 120

function commandRequest(intent: CommandIntent): { path: `/${string}`; body: object } {
  if (intent.kind === 'reconcile') {
    return { path: '/core/commands/reconcile', body: { asset_id: intent.asset?.id ?? null } }
  }
  if (intent.kind === 'close') {
    return {
      path: `/assets/${intent.asset.id}/commands/close`,
      body: { confirmation: intent.confirmation },
    }
  }
  return { path: `/assets/${intent.asset.id}/commands/${intent.kind}`, body: {} }
}

function statusLabel(status: CommandStatusResponse['status']) {
  if (status === 'pending') return 'Waiting for core'
  if (status === 'processing') return 'Processing'
  if (status === 'completed') return 'Completed successfully'
  return 'Command failed'
}

export function CommandsPage() {
  const assetsQuery = useAssets()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [commandId, setCommandId] = useState<number | null>(null)
  const [closeOpen, setCloseOpen] = useState(false)
  const [closeConfirmation, setCloseConfirmation] = useState('')
  const pollAttempts = useRef(0)
  const [pollAttemptCount, setPollAttemptCount] = useState(0)
  const assets = assetsQuery.data?.items ?? []
  const selectedAsset = assets.find((asset) => asset.id === selectedId) ?? assets[0] ?? null

  const commandQuery = useQuery({
    queryKey: ['command', commandId],
    queryFn: () => {
      pollAttempts.current += 1
      setPollAttemptCount(pollAttempts.current)
      return apiClient.get<CommandStatusResponse>(`/commands/${commandId}`)
    },
    enabled: commandId !== null,
    retry: false,
    refetchInterval: (query) => {
      if (query.state.status === 'error') return false
      const status = query.state.data?.status
      if (status === 'completed' || status === 'failed') return false
      return pollAttempts.current < MAX_POLL_ATTEMPTS ? POLL_INTERVAL_MS : false
    },
  })

  const mutation = useMutation({
    mutationFn: (intent: CommandIntent) => {
      const request = commandRequest(intent)
      return apiClient.post<CommandEnqueueResponse>(
        request.path,
        request.body,
        { idempotencyKey: crypto.randomUUID() },
      )
    },
    onMutate: () => {
      pollAttempts.current = 0
      setPollAttemptCount(0)
      setCommandId(null)
    },
    onSuccess: (response) => {
      pollAttempts.current = 0
      setPollAttemptCount(0)
      setCommandId(response.id)
      setCloseOpen(false)
      setCloseConfirmation('')
    },
    retry: false,
  })
  const commandInFlight = commandId !== null && (
    commandQuery.data === undefined || ['pending', 'processing'].includes(commandQuery.data.status)
  )
  const pollingLimitReached = commandQuery.data !== undefined
    && ['pending', 'processing'].includes(commandQuery.data.status)
    && pollAttemptCount >= MAX_POLL_ATTEMPTS
  const controlsLocked = mutation.isPending || commandInFlight

  if (assetsQuery.isPending) return <LoadingPanel label="Loading command controls" />
  if (assetsQuery.isError) return <ErrorPanel onRetry={() => void assetsQuery.refetch()} />

  return (
    <div className="space-y-6">
      <header>
        <p className="text-sm font-semibold text-[#9d7cff]">CONTROL PLANE</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight text-white">Durable commands</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
          Submit durable intents to the local queue. Acceptance does not mean execution: the trading core processes each command and reports its terminal result.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)]">
        {selectedAsset === null ? (
          <section aria-labelledby="command-controls-heading" className="rounded-xl border border-[#253047] bg-[#111722] p-5 sm:p-6">
            <h2 className="text-lg font-semibold text-white" id="command-controls-heading">Core command controls</h2>
            <div className="mt-4"><EmptyState title="No assets configured" detail="Asset-specific commands need an API-backed asset. Core-wide reconciliation remains available." /></div>
            <button
              aria-label="Reconcile all assets"
              className="mt-4 w-full rounded-lg border border-[#35405a] bg-[#121927] px-4 py-3 text-left text-sm font-semibold text-slate-200 transition hover:bg-[#172031] disabled:cursor-not-allowed disabled:opacity-50"
              disabled={controlsLocked}
              onClick={() => mutation.mutate({ kind: 'reconcile', asset: null })}
              type="button"
            >
              Reconcile all assets
              <span className="mt-1 block text-xs font-normal text-slate-500">Queue a core-wide reconciliation.</span>
            </button>
          </section>
        ) : (
          <section aria-labelledby="command-controls-heading" className="rounded-xl border border-[#253047] bg-[#111722] p-5 sm:p-6">
            <div className="flex flex-col gap-4 border-b border-[#253047] pb-5 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <h2 className="text-lg font-semibold text-white" id="command-controls-heading">Asset command controls</h2>
                <p className="mt-1 text-sm text-slate-500">Select an API-backed asset before submitting an intent.</p>
              </div>
              <label className="text-xs font-medium uppercase tracking-wide text-slate-500">Selected asset
                <select
                  aria-label="Selected asset"
                  className="mt-2 w-full rounded-lg border border-[#303a50] bg-[#0b1019] px-3 py-2.5 text-sm text-white outline-none focus:border-[#7132f5] sm:w-56"
                  onChange={(event) => setSelectedId(Number(event.target.value))}
                  value={selectedAsset.id}
                >
                  {assets.map((asset) => <option key={asset.id} value={asset.id}>{asset.symbol} · #{asset.id}</option>)}
                </select>
              </label>
            </div>

            <div className="mt-5 rounded-lg border border-[#29344a] bg-[#0d131e] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><p className="font-semibold text-white">{selectedAsset.symbol}</p><p className="mt-1 text-xs text-slate-500">Asset ID #{selectedAsset.id} · Exchange #{selectedAsset.exchange_config_id}</p></div>
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ${selectedAsset.enabled ? 'bg-emerald-500/10 text-emerald-400' : 'bg-slate-500/10 text-slate-400'}`}>
                  {selectedAsset.enabled ? 'Enabled for trading' : 'Disabled for trading'}
                </span>
              </div>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <button
                aria-label={`Pause ${selectedAsset.symbol}`}
                className="rounded-lg border border-amber-700/70 bg-amber-500/10 px-4 py-3 text-left text-sm font-semibold text-amber-300 transition hover:bg-amber-500/15 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={controlsLocked}
                onClick={() => mutation.mutate({ kind: 'pause', asset: selectedAsset })}
                type="button"
              >
                Pause {selectedAsset.symbol}
                <span className="mt-1 block text-xs font-normal text-amber-200/60">Queue a pause intent for this asset.</span>
              </button>
              <button
                aria-label={`Resume ${selectedAsset.symbol}`}
                className="rounded-lg border border-emerald-700/70 bg-emerald-500/10 px-4 py-3 text-left text-sm font-semibold text-emerald-300 transition hover:bg-emerald-500/15 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={controlsLocked}
                onClick={() => mutation.mutate({ kind: 'resume', asset: selectedAsset })}
                type="button"
              >
                Resume {selectedAsset.symbol}
                <span className="mt-1 block text-xs font-normal text-emerald-200/60">Queue a resume intent for this asset.</span>
              </button>
              <button
                aria-label={`Reconcile ${selectedAsset.symbol}`}
                className="rounded-lg border border-[#51417d] bg-[#17132a] px-4 py-3 text-left text-sm font-semibold text-[#b39aff] transition hover:bg-[#1d1735] disabled:cursor-not-allowed disabled:opacity-50"
                disabled={controlsLocked}
                onClick={() => mutation.mutate({ kind: 'reconcile', asset: selectedAsset })}
                type="button"
              >
                Reconcile {selectedAsset.symbol}
                <span className="mt-1 block text-xs font-normal text-slate-500">Queue reconciliation for this asset.</span>
              </button>
              <button
                aria-label="Reconcile all assets"
                className="rounded-lg border border-[#35405a] bg-[#121927] px-4 py-3 text-left text-sm font-semibold text-slate-200 transition hover:bg-[#172031] disabled:cursor-not-allowed disabled:opacity-50"
                disabled={controlsLocked}
                onClick={() => mutation.mutate({ kind: 'reconcile', asset: null })}
                type="button"
              >
                Reconcile all assets
                <span className="mt-1 block text-xs font-normal text-slate-500">Queue a core-wide reconciliation.</span>
              </button>
              <button
                aria-label={`Close position for ${selectedAsset.symbol}`}
                className="rounded-lg border border-red-800/80 bg-red-950/30 px-4 py-3 text-left text-sm font-semibold text-red-300 transition hover:bg-red-950/50 disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-2"
                disabled={controlsLocked}
                onClick={() => { setCloseConfirmation(''); setCloseOpen(true) }}
                type="button"
              >
                Close position
                <span className="mt-1 block text-xs font-normal text-red-200/60">Destructive: request that core close the selected asset position.</span>
              </button>
            </div>

            {closeOpen && (
              <section aria-labelledby="close-dialog-title" className="mt-5 rounded-xl border border-red-800 bg-red-950/25 p-5" role="alertdialog">
                <h3 className="text-lg font-semibold text-red-200" id="close-dialog-title">Confirm close position</h3>
                <p className="mt-2 text-sm leading-6 text-red-200/80" role="alert">
                  This can place a reduce-only order when processed by the core. API acceptance is not execution success.
                </p>
                <p className="mt-3 text-sm text-slate-300">Type <code className="rounded bg-black/30 px-1.5 py-1 font-mono text-white">CLOSE {selectedAsset.id}</code> exactly to continue.</p>
                <label className="mt-4 block text-sm font-medium text-slate-200" htmlFor="close-confirmation">Type CLOSE {selectedAsset.id} to confirm</label>
                <input
                  autoComplete="off"
                  className="mt-2 w-full rounded-lg border border-red-900 bg-[#0b1019] px-3 py-2.5 font-mono text-sm text-white outline-none focus:border-red-500"
                  id="close-confirmation"
                  onChange={(event) => setCloseConfirmation(event.target.value)}
                  value={closeConfirmation}
                />
                <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                  <button className="rounded-lg border border-[#35405a] px-4 py-2 text-sm font-semibold text-slate-300" onClick={() => { setCloseOpen(false); setCloseConfirmation('') }} type="button">Cancel</button>
                  <button
                    className="rounded-lg bg-red-700 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={controlsLocked || closeConfirmation !== `CLOSE ${selectedAsset.id}`}
                    onClick={() => mutation.mutate({ kind: 'close', asset: selectedAsset, confirmation: closeConfirmation })}
                    type="button"
                  >Submit close command</button>
                </div>
              </section>
            )}
          </section>
        )}

          <section aria-labelledby="activity-heading" aria-live="polite" className="rounded-xl border border-[#253047] bg-[#111722] p-5 sm:p-6">
            <h2 className="text-lg font-semibold text-white" id="activity-heading">Command activity</h2>
            {mutation.isPending && <p className="mt-4 text-sm text-slate-300" role="status">Command in progress. Submit controls are locked.</p>}
            {mutation.isError && <div className="mt-4" role="alert">
              <p className="font-medium text-red-300">Command was not accepted</p>
              <p className="mt-1 text-sm text-slate-500">The local API request failed. Retrying submits a new durable intent with a fresh key.</p>
              {mutation.variables && <button
                aria-label="Retry command submission"
                className="mt-3 rounded-lg bg-[#7132f5] px-3 py-2 text-sm font-semibold text-white hover:bg-[#6428dd]"
                onClick={() => mutation.mutate(mutation.variables)}
                type="button"
              >Retry submission</button>}
            </div>}
            {mutation.isSuccess && <p className="mt-4 text-sm font-medium text-[#b39aff]">Command accepted</p>}
            {commandQuery.isError && <div className="mt-3" role="alert">
              <p className="text-sm text-red-300">Command status could not be loaded.</p>
              <p className="mt-1 text-xs leading-5 text-slate-500">The command may still be running. Submit controls remain locked to prevent a duplicate intent.</p>
              <button
                className="mt-3 rounded-lg border border-[#51417d] bg-[#17132a] px-3 py-2 text-sm font-semibold text-[#b39aff] hover:bg-[#1d1735]"
                onClick={() => { pollAttempts.current = 0; setPollAttemptCount(0); void commandQuery.refetch() }}
                type="button"
              >Retry command status</button>
            </div>}
            {pollingLimitReached && !commandQuery.isError && <div className="mt-3" role="alert">
              <p className="text-sm text-amber-300">Automatic status polling paused.</p>
              <p className="mt-1 text-xs leading-5 text-slate-500">The command is still non-terminal after two minutes. Submit controls remain locked to prevent a duplicate intent.</p>
              <button
                className="mt-3 rounded-lg border border-[#51417d] bg-[#17132a] px-3 py-2 text-sm font-semibold text-[#b39aff] hover:bg-[#1d1735]"
                onClick={() => { pollAttempts.current = 0; setPollAttemptCount(0); void commandQuery.refetch() }}
                type="button"
              >Retry command status</button>
            </div>}
            {commandQuery.data && (
              <div className="mt-4 rounded-lg border border-[#29344a] bg-[#0d131e] p-4">
                <p className="text-xs uppercase tracking-wide text-slate-500">Command #{commandQuery.data.id} · {commandQuery.data.type}</p>
                <p className={`mt-2 font-semibold ${commandQuery.data.status === 'failed' ? 'text-red-300' : commandQuery.data.status === 'completed' ? 'text-emerald-400' : 'text-amber-300'}`}>
                  {statusLabel(commandQuery.data.status)}
                </p>
                {commandQuery.data.status === 'failed' && <p className="mt-2 text-sm text-slate-400">Command processing failed. Review core health before retrying.</p>}
              </div>
            )}
            {!mutation.isPending && !mutation.isSuccess && !mutation.isError && <p className="mt-4 text-sm leading-6 text-slate-500">No command submitted in this session. Results are shown only after the API accepts an intent.</p>}
          </section>
      </div>
    </div>
  )
}
