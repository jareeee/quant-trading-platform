export function LoadingPanel({ label = 'Loading data' }: { label?: string }) {
  return (
    <div aria-label={label} className="grid animate-pulse gap-3" role="status">
      <span className="h-24 rounded-lg bg-[#151c2b]" />
      <span className="h-40 rounded-lg bg-[#151c2b]" />
      <span className="sr-only">{label}</span>
    </div>
  )
}

export function ErrorPanel({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="rounded-xl border border-red-900/70 bg-[#111722] p-6" role="alert">
      <h2 className="text-lg font-semibold text-white">Data could not be loaded</h2>
      <p className="mt-2 text-sm text-slate-400">The local API did not return the requested data.</p>
      <button className="mt-4 rounded-lg bg-[#7132f5] px-4 py-2 text-sm font-semibold text-white hover:bg-[#6428dd]" onClick={onRetry} type="button">
        Retry
      </button>
    </section>
  )
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[#2b3447] px-5 py-10 text-center">
      <p className="font-medium text-slate-200">{title}</p>
      <p className="mt-1 text-sm text-slate-500">{detail}</p>
    </div>
  )
}
