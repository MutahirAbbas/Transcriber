import type { ComponentType } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { useMe } from '../api/hooks'
import { AgentsIcon, CallsIcon, LogoIcon, ScorecardIcon, SettingsIcon } from './Icons'
import { TranscriberStatus } from './TranscriberStatus'

const item = 'flex items-center gap-2.5 px-2.5 py-2.5 rounded-control min-h-tap'

// Not in v1: shown so the nav matches the design, but disabled with no route.
const STUBS: [string, ComponentType][] = [
  ['Agents', AgentsIcon],
  ['Scorecard', ScorecardIcon],
  ['Settings', SettingsIcon],
]

export function Sidebar() {
  const { data: me } = useMe()
  return (
    <nav
      aria-label="Main"
      className="bg-surface border-b md:border-b-0 md:border-r border-border px-4 py-4 md:py-6 flex flex-wrap md:flex-nowrap md:flex-col items-center md:items-stretch gap-1.5 md:w-[220px] md:shrink-0"
    >
      <div className="flex items-center gap-2.5 px-2 pt-1 md:pb-5 mr-auto md:mr-0">
        <div className="w-7 h-7 rounded-logo bg-ink text-surface flex items-center justify-center">
          <LogoIcon />
        </div>
        <span className="font-semibold text-h2 tracking-tight">Call Analyzer</span>
      </div>

      {/* the only live section — the detail page belongs to Calls too */}
      <Link to="/" aria-current="page" className={`${item} bg-border-subtle text-ink hover:text-ink font-medium`}>
        <CallsIcon />
        Calls
      </Link>
      {STUBS.map(([label, StubIcon]) => (
        <button
          key={label}
          type="button"
          disabled
          title="Not available yet"
          className={`${item} text-nav opacity-60 cursor-not-allowed text-left`}
        >
          <StubIcon />
          {label}
        </button>
      ))}

      <div className="hidden md:block grow" />
      <TranscriberStatus />
      {me?.email && (
        <div className="basis-full md:basis-auto px-2.5 flex items-center justify-between gap-2 text-sm">
          <span className="truncate text-muted" title={me.email}>
            {me.email}
          </span>
          <button type="button" onClick={signOut}
                  className="shrink-0 min-h-tap md:min-h-8 text-accent hover:text-accent-hover cursor-pointer">
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
