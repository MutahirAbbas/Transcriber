import { Link } from 'react-router-dom'

import { useAgents, useAssign, useCalls, useCancel, useStats } from '../../api/hooks'
import type { Agent, CallSummary, Filter } from '../../api/types'
import { formatAgo, formatDuration, formatWhen } from '../../lib/format'
import { isInProgress, stageText } from '../../lib/stage'
import { btnSecondary, card, segOff, segOn, segTrack } from '../../lib/ui'
import { Bar, FlagsChip, OutcomeBadge, ScoreCell } from '../Badges'
import { SearchIcon } from '../Icons'

const COLUMNS: [string, string][] = [
  ['Call', 'w-[27%]'],
  ['Agent', 'w-[11%]'],
  ['Outcome', 'w-[10%]'],
  ['Length', 'w-[8.5%]'],
  ['Score', 'w-[16.5%]'],
  ['Flags', 'w-[7.5%]'],
  ['Weakest', 'w-[12%]'],
]
export const PAGE_SIZE = 25

interface Props {
  filter: Filter
  onFilter: (f: Filter) => void
  search: string
  onSearch: (s: string) => void
  q: string // debounced search
  page: number
  onPage: (p: number) => void
}

const detailPath = (id: string) => `/calls/${encodeURIComponent(id)}`

/** Rows that show a stage instead of results. A failed re-score still has its old results. */
const showsStage = (c: CallSummary) => isInProgress(c) || (c.status === 'error' && c.overall_score == null)

const NEW_AGENT = 'new:'

/** A dotted-underlined "Assign agent" that's really a native select: inline, keyboard- and
 *  phone-friendly, and can't be clipped by the table. The name heard on the call comes first. */
function AssignAgent({ call, agents }: { call: CallSummary; agents: Agent[] }) {
  const assign = useAssign()
  const heard = call.suggested_agent
  const heardAgent = heard ? agents.find((a) => a.name.toLowerCase() === heard.trim().toLowerCase()) : undefined
  const others = agents.filter((a) => a !== heardAgent)
  return (
    <>
      <label className="relative inline-block cursor-pointer text-muted hover:text-ink border-b border-dotted border-current leading-snug">
        {assign.isPending ? 'Assigning…' : 'Assign agent'}
        <select
          aria-label={`Assign an agent to call ${call.id}`}
          className="absolute inset-0 w-full opacity-0 cursor-pointer"
          value=""
          disabled={assign.isPending}
          onChange={(e) => {
            const v = e.target.value
            if (v.startsWith(NEW_AGENT)) assign.mutate({ callId: call.id, name: v.slice(NEW_AGENT.length) })
            else if (v) assign.mutate({ callId: call.id, agentId: Number(v) })
          }}
        >
          <option value="" disabled>
            Assign agent
          </option>
          {heard && (
            <option value={heardAgent ? heardAgent.id : `${NEW_AGENT}${heard}`}>{heard} (heard in call)</option>
          )}
          {others.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      {assign.isError && <span role="alert" className="block text-sm text-rust">{assign.error.message}</span>}
    </>
  )
}

function AgentCell({ call, agents }: { call: CallSummary; agents: Agent[] }) {
  return call.agent ? <>{call.agent.name}</> : <AssignAgent call={call} agents={agents} />
}

function RowAction({ call }: { call: CallSummary }) {
  const cancel = useCancel()
  // a call with a score is open-only: cancelling would throw that score away
  if (showsStage(call) && call.overall_score == null) {
    return (
      <>
        <button
          type="button"
          className="text-muted hover:text-ink cursor-pointer min-h-tap md:min-h-0"
          disabled={cancel.isPending}
          aria-label={`Cancel call ${call.id}`}
          onClick={() => cancel.mutate(call.id)}
        >
          {cancel.isPending ? 'Cancelling…' : 'Cancel'}
        </button>
        {cancel.isError && <span role="alert" className="block text-sm text-rust">{cancel.error.message}</span>}
      </>
    )
  }
  return (
    <Link to={detailPath(call.id)} aria-label={`Open call ${call.id}`}>
      Open
    </Link>
  )
}

function StageLine({ call }: { call: CallSummary }) {
  const failed = call.status === 'error'
  const pct = Math.round(call.progress * 100)
  return (
    <div className="flex items-center gap-4 min-w-0">
      {!failed && (
        <div
          role="progressbar"
          aria-label={`${call.id} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          className="w-40 shrink-0"
        >
          <Bar value={call.progress} className="bg-link" />
        </div>
      )}
      <span className={`text-md min-w-0 ${failed ? 'text-rust' : 'text-muted'}`} title={call.error ?? undefined}>
        <span className={failed ? 'line-clamp-2 break-words' : ''}>{stageText(call)}</span>
      </span>
    </div>
  )
}

function CallId({ call }: { call: CallSummary }) {
  const stage = showsStage(call)
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <Link
        to={detailPath(call.id)}
        title={call.id}
        className={`font-mono text-md truncate hover:underline ${stage ? 'text-muted hover:text-muted' : 'text-ink hover:text-ink'}`}
      >
        {call.id}
      </Link>
      <span className="text-md text-muted">
        {stage ? `Uploaded ${formatAgo(call.uploaded_at)}` : formatWhen(call.uploaded_at)}
      </span>
    </div>
  )
}

const td = 'px-3 first:pl-5 md:first:pl-5 py-3.5 align-middle'

function TableRow({ call, agents, target }: { call: CallSummary; agents: Agent[]; target: number }) {
  const stage = showsStage(call)
  return (
    <tr className={`border-t border-divider ${stage ? 'bg-faint' : ''}`}>
      <td className={td}>
        <CallId call={call} />
      </td>
      <td className={`${td} whitespace-nowrap`}>
        <AgentCell call={call} agents={agents} />
      </td>
      {stage ? (
        <td className={td} colSpan={4}>
          <StageLine call={call} />
        </td>
      ) : (
        <>
          <td className={td}>
            <OutcomeBadge outcome={call.outcome} />
          </td>
          <td className={`${td} font-mono text-md`}>{formatDuration(call.duration_s)}</td>
          <td className={td}>
            {call.overall_score != null ? <ScoreCell score={call.overall_score} target={target} /> : <span className="text-muted">—</span>}
          </td>
          <td className={td}>
            <FlagsChip count={call.flag_count} />
          </td>
        </>
      )}
      <td className={`${td} whitespace-nowrap text-muted truncate`}>{stage ? '—' : (call.weakest_label ?? '—')}</td>
      <td className={`${td} pr-5 text-right whitespace-nowrap`}>
        <RowAction call={call} />
      </td>
    </tr>
  )
}

/** Phone width: the same row as a small stack, so nothing needs sideways scrolling. */
function PhoneRow({ call, agents, target }: { call: CallSummary; agents: Agent[]; target: number }) {
  const stage = showsStage(call)
  return (
    <li className={`px-4 py-4 flex flex-col gap-2.5 border-t border-divider ${stage ? 'bg-faint' : ''}`}>
      <div className="flex items-start justify-between gap-3 min-w-0">
        <CallId call={call} />
        <span className="shrink-0">
          <RowAction call={call} />
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <AgentCell call={call} agents={agents} />
        {!stage && <OutcomeBadge outcome={call.outcome} />}
        <span className="font-mono text-md text-muted">{formatDuration(call.duration_s)}</span>
      </div>
      {stage ? (
        <StageLine call={call} />
      ) : (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {call.overall_score != null && <ScoreCell score={call.overall_score} target={target} />}
          <FlagsChip count={call.flag_count} />
          {call.weakest_label && <span className="text-muted">{call.weakest_label}</span>}
        </div>
      )}
    </li>
  )
}

export function CallsTable({ filter, onFilter, search, onSearch, q, page, onPage }: Props) {
  const { data, isLoading, error } = useCalls({ filter, q, page, pageSize: PAGE_SIZE })
  const { data: agents = [] } = useAgents()
  const target = useStats().data?.target_score ?? 60
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const filters: [Filter, string][] = [
    ['all', 'All'],
    ['sales', 'Sales'],
    ['flagged', 'Flagged'],
    ['below_target', `Below ${target}`],
    ['unassigned', 'Unassigned'],
  ]

  let empty: string | null = null
  if (isLoading) empty = 'Loading calls…'
  else if (error) empty = `Couldn't load calls: ${error.message}`
  else if (!items.length) empty = filter === 'all' && !q ? 'No calls yet. Drop recordings above to get started.' : 'No calls match.'

  return (
    <section aria-label="Calls" className={`${card} flex flex-col overflow-hidden`}>
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 md:px-5 py-3.5">
        <div className={segTrack} role="group" aria-label="Filter calls">
          {filters.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => onFilter(value)}
              className={filter === value ? segOn : segOff}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2.5 border border-border rounded-control px-3 min-h-tap md:min-h-10 flex-[1_1_220px] md:flex-[0_1_280px] min-w-0 text-muted focus-within:border-link">
          <SearchIcon />
          <span className="sr-only">Search calls</span>
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search agent, outcome, or a phrase in the call"
            className="border-0 outline-none flex-1 min-w-0 text-md text-ink bg-transparent placeholder:text-muted"
          />
        </label>
      </div>

      {empty ? (
        <p className="m-0 px-5 py-10 border-t border-divider text-muted text-center">{empty}</p>
      ) : (
        <>
          <table className="hidden lg:table w-full table-fixed border-collapse">
            <caption className="sr-only">Calls, newest first</caption>
            <thead>
              <tr className="text-left text-sm text-muted uppercase tracking-head">
                {COLUMNS.map(([c, width]) => (
                  <th key={c} scope="col" className={`px-3 first:pl-5 py-3 font-medium border-t border-divider ${width}`}>
                    {c}
                  </th>
                ))}
                <th scope="col" className="px-3 pr-5 py-3 border-t border-divider">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <TableRow key={c.id} call={c} agents={agents} target={target} />
              ))}
            </tbody>
          </table>
          <ul className="lg:hidden m-0 p-0 list-none">
            {items.map((c) => (
              <PhoneRow key={c.id} call={c} agents={agents} target={target} />
            ))}
          </ul>
        </>
      )}

      <div className="flex justify-between items-center gap-3 px-4 md:px-5 py-3 border-t border-divider text-md text-muted">
        <span>
          Showing {items.length} of {total}
        </span>
        <div className="flex gap-1.5">
          <button type="button" className={`${btnSecondary} !font-normal !px-3.5 text-md text-muted`} disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Prev
          </button>
          <button type="button" className={`${btnSecondary} !font-normal !px-3.5 text-md text-muted`} disabled={page >= lastPage} onClick={() => onPage(page + 1)}>
            Next
          </button>
        </div>
      </div>
    </section>
  )
}
