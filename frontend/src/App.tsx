import { Link, NavLink, Route, Routes } from 'react-router-dom'

import { DashboardPage } from './pages/DashboardPage'
import { AssetsPage } from './pages/AssetsPage'
import { AssetDetailPage } from './pages/AssetDetailPage'

function PlaceholderPage({ title }: { title: string }) {
  return <h1 className="text-2xl font-semibold">{title}</h1>
}

function NotFoundPage() {
  return (
    <section>
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <Link className="mt-4 inline-block text-[#9d7cff]" to="/">Return to dashboard</Link>
    </section>
  )
}

const navClass = ({ isActive }: { isActive: boolean }) => `border-b-2 px-1 py-5 text-sm font-medium transition-colors ${isActive ? 'border-[#7132f5] text-white' : 'border-transparent text-slate-400 hover:text-white'}`

export function App() {
  return (
    <div className="min-h-screen bg-[#090d15] text-slate-100">
      <header className="border-b border-[#20293a] bg-[#0c111b]">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-8 px-4 sm:px-6">
          <Link className="flex items-center gap-3 py-4" to="/" aria-label="Axiom Trading dashboard">
            <span aria-hidden="true" className="grid size-8 place-items-center rounded-lg border border-[#7132f5] bg-[#15102a] text-sm font-bold text-[#a98cff]">A</span>
            <span><strong className="block text-sm tracking-wide text-white">AXIOM</strong><span className="block text-[10px] tracking-[0.18em] text-slate-500">TRADING CORE</span></span>
          </Link>
          <nav aria-label="Primary navigation" className="order-3 w-full sm:order-none sm:w-auto">
            <ul className="flex gap-6">
              <li><NavLink className={navClass} end to="/">Dashboard</NavLink></li>
              <li><NavLink className={navClass} to="/assets">Assets</NavLink></li>
              <li><NavLink className={navClass} to="/commands">Commands</NavLink></li>
            </ul>
          </nav>
          <span className="hidden text-xs text-slate-500 md:block">Local control plane</span>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:py-10">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/assets" element={<AssetsPage />} />
          <Route path="/assets/:assetId" element={<AssetDetailPage />} />
          <Route path="/commands" element={<PlaceholderPage title="Commands" />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  )
}
