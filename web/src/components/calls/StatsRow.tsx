import type { ReactNode } from 'react'

import { useStats } from '../../api/hooks'
import { card } from '../../lib/ui'

function Stat({ label, sub, children }: { label: string; sub: ReactNode; children: ReactNode }) {
  return (
    <div className="px-5 md:px-6 py-5 flex flex-col gap-2 min-w-0">
      <span className="text-md text-muted">{label}</span>
      {children}
      <span className="text-sm text-muted">{sub}</span>
    </div>
  )
}

const big = 'text-h2 md:text-stat font-medium tracking-snug leading-none truncate'

export function StatsRow() {
  const { data } = useStats()
  const target = data?.target_score ?? 60
  const below = data?.avg_score != null && data.avg_score < target
  const running = data?.in_progress ?? 0
  const flagged = data?.flagged_calls ?? 0
  return (
    <section
      aria-label="This week"
      className={`${card} grid grid-cols-2 lg:grid-cols-4 divide-y lg:divide-y-0 divide-divider [&>*]:border-divider [&>*:nth-child(odd)]:border-r lg:[&>*:not(:last-child)]:border-r [&>*:nth-child(-n+2)]:!border-t-0`}
    >
      <Stat label="Calls scored this week" sub={running ? `${running} still transcribing` : 'nothing in progress'}>
        <span className={big}>{data ? data.calls_scored : '—'}</span>
      </Stat>
      <Stat
        label="Average score"
        sub={
          data?.avg_score == null ? (
            `target ${target}`
          ) : below ? (
            <span className="text-rust">Below the {target} target</span>
          ) : (
            `At or above the ${target} target`
          )
        }
      >
        <span className={big}>
          {data?.avg_score ?? '—'}
          <span className="text-base text-muted font-normal tracking-normal"> / 100</span>
        </span>
      </Stat>
      <Stat label="Compliance flags" sub={`across ${flagged} ${flagged === 1 ? 'call' : 'calls'}`}>
        <span className={big}>{data ? data.compliance_flags : '—'}</span>
      </Stat>
      <Stat label="Weakest category" sub={data?.weakest_avg != null ? `avg ${data.weakest_avg} / 10` : '—'}>
        <span className={`${big} max-md:text-h3`} title={data?.weakest_label ?? undefined}>{data?.weakest_label ?? '—'}</span>
      </Stat>
    </section>
  )
}
