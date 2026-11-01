import type { ComponentType } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { useMe } from '../api/hooks'
import { AgentsIcon, CallsIcon, LogoIcon, ScorecardIcon, SettingsIcon } from './Icons'
import { TranscriberStatus } from './TranscriberStatus'

const item = 'flex items-center gap-3 px-3 py-2.5 rounded-control min-h-tap'

// Phase 2: shown so the nav matches the design, disabled until then.
const COMING_SOON: [string, ComponentType][] = [
  ['Agents', AgentsIcon],
  ['Scorecard', ScorecardIcon],
  ['Settings', SettingsIcon],
]

export function Sidebar() {
  const { data: me } = useMe()
  return (
    <nav
      aria-label="Main"
      className="px-4 md:px-5 py-4 md:py-7 flex flex-wrap md:flex-nowrap md:flex-col md:items-stretch items-center gap-1 md:w-[224px] md:shrink-0"
    >
      <div className="flex items-center gap-2 px-1 md:pb-5 basis-full md:basis-auto">
        <LogoIcon />
        <span className="font-medium text-lg tracking-tight">Call Analyzer</span>
      </div>

      {/* the only live section — the detail page belongs to Calls too */}
      <Link to="/" aria-current="page" className={`${item} bg-surface border border-border text-ink hover:text-ink`}>
        <CallsIcon />
        Calls
      </Link>
      {COMING_SOON.map(([label, Icon]) => (
        <button key={label} type="button" disabled title="Coming soon" className={`${item} text-muted cursor-not-allowed text-left`}>
          <Icon />
          <span className="hidden sm:inline">{label}</span>
          <span className="sr-only sm:hidden">{label}</span>
        </button>
      ))}

      <div className="hidden md:block grow" />
      <TranscriberStatus />
      {me?.email && (
        <div className="basis-full md:basis-auto mt-2 pt-3 md:mt-3 md:pt-4 border-t border-border flex items-center justify-between gap-2 text-md">
          <span className="truncate text-muted" title={me.email}>
            {me.email}
          </span>
          <button type="button" onClick={signOut} className="shrink-0 min-h-tap md:min-h-6 text-link hover:text-link-ink cursor-pointer">
            Sign out
          </button>
        </div>
      )}
    </nav>
  )
}

async function signOut() {
  try {
    await api.logout()
  } finally {
    window.location.assign('/login') // full reload: drops every cached query
  }
}
