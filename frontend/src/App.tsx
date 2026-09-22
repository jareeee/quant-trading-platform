import { Link, NavLink, Route, Routes, useParams } from 'react-router-dom'

function PlaceholderPage({ title }: { title: string }) {
  return <h1 className="text-2xl font-semibold">{title}</h1>
}

function AssetDetailPage() {
  const { assetId } = useParams<{ assetId: string }>()

  return <PlaceholderPage title={`Asset ${assetId ?? ''}`.trim()} />
}

function NotFoundPage() {
  return (
    <section>
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link className="mt-4 inline-block text-sky-400" to="/">
        Return to dashboard
      </Link>
    </section>
  )
}

export function App() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <nav aria-label="Primary navigation">
          <ul className="flex gap-6">
            <li>
              <NavLink to="/">Dashboard</NavLink>
            </li>
            <li>
              <NavLink to="/assets">Assets</NavLink>
            </li>
            <li>
              <NavLink to="/commands">Commands</NavLink>
            </li>
          </ul>
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route path="/" element={<PlaceholderPage title="Dashboard" />} />
          <Route path="/assets" element={<PlaceholderPage title="Assets" />} />
          <Route path="/assets/:assetId" element={<AssetDetailPage />} />
          <Route path="/commands" element={<PlaceholderPage title="Commands" />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  )
}
