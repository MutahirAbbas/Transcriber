import type { ReactNode } from 'react'

import { useStats } from '../../api/hooks'
import { categoryLabel } from '../../lib/format'
import { smallCard } from '../../lib/ui'

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={`${smallCard} px-[18px] py-4 flex flex-col gap-1 min-w-0`}>
      <span className="text-sm text-muted">{label}</span>
      {children}
    </div>
  )
}

const big = 'text-h1 font-semibold tracking-tightest'

export function StatsRow() {
  const { data } = useStats()
  const flags = data?.compliance_flags ?? 0
  return (
    <section aria-label="This week" className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <Stat label="Calls scored this week">
        <span className={big}>{data ? data.calls_scored : '—'}</span>
      </Stat>
      <Stat label="Average score">
        <span className={big}>
          {data?.avg_score ?? '—'}
          <span className="text-base text-muted font-normal"> / 100</span>
        </span>
      </Stat>
      <Stat label="Compliance flags">
        <span className={`${big} ${flags > 0 ? 'text-red' : ''}`}>{data ? flags : '—'}</span>
      </Stat>
      <Stat label="Weakest category">
        <span className="text-lg font-semibold tracking-tight pt-1">
          {data?.weakest_category ? categoryLabel(data.weakest_category) : '—'}
        </span>
      </Stat>
    </section>
  )
}
