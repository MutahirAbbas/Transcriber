import { Link } from 'react-router-dom'

import { useCalls } from '../../api/hooks'
import type { CallSummary, Filter } from '../../api/types'
import { categoryLabel, formatDuration, formatWhen } from '../../lib/format'
import { btnSecondary, card, pillOff, pillOn } from '../../lib/ui'
import { FlagsChip, OutcomeBadge, ScoreCell } from '../Badges'
import { SearchIcon } from '../Icons'

const FILTERS: [Filter, string][] = [
  ['all', 'All'],
  ['sales', 'Sales'],
  ['flagged', 'Flagged'],
  ['below60', 'Below 60'],
]
const COLUMNS = ['Call', 'Agent', 'Outcome', 'Length', 'Score', 'Flags', 'Weakest']
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

function ScoreColumn({ call }: { call: CallSummary }) {
  if (call.status === 'error')
    return (
      <span className="text-red" title={call.error ?? undefined}>
        Error
      </span>
    )
  if (call.status !== 'done' || call.overall_score == null) return <span className="text-muted">Processing</span>
  return <ScoreCell score={call.overall_score} />
}

const td = 'px-[18px] py-3.5'

export function CallsTable({ filter, onFilter, search, onSearch, q, page, onPage }: Props) {
  const { data, isLoading, error } = useCalls({ filter, q, page, pageSize: PAGE_SIZE })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))

  let empty: string | null = null
  if (isLoading) empty = 'Loading calls…'
  else if (error) empty = `Couldn't load calls: ${error.message}`
  else if (!items.length) empty = filter === 'all' && !q ? 'No calls yet. Drop recordings above to get started.' : 'No calls match.'

  return (
    <section aria-label="Scored calls" className={`${card} overflow-hidden flex flex-col`}>
      <div className="flex flex-wrap items-center justify-between gap-3 px-[18px] py-3.5 border-b border-border">
        <div className="flex gap-1.5 flex-wrap" role="group" aria-label="Filter calls">
          {FILTERS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => onFilter(value)}
              className={filter === value ? pillOn : pillOff}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="relative flex items-center gap-2 border border-control rounded-control px-3 min-h-tap md:min-h-10 flex-[1_1_200px] sm:flex-[0_1_280px] min-w-[200px] text-muted focus-within:border-accent">
          <SearchIcon />
          <span className="sr-only">Search calls</span>
          <input
            type="search"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search agent, outcome, phrase…"
            className="border-0 outline-none flex-1 min-w-0 text-ink bg-transparent placeholder:text-muted"
          />
        </label>
      </div>

      {/* relative: the sr-only (absolute) caption/labels must not escape the scroller and widen the page */}
      <div className="relative overflow-x-auto">
        <table className="w-full border-collapse min-w-[880px]">
          <caption className="sr-only">Calls, newest first</caption>
          <thead>
            <tr className="text-left text-sm text-muted uppercase tracking-head">
              {COLUMNS.map((c) => (
                <th key={c} scope="col" className="px-[18px] py-3 font-medium border-b border-border">
                  {c}
                </th>
              ))}
              <th scope="col" className="px-[18px] py-3 border-b border-border">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {empty ? (
              <tr>
                <td colSpan={COLUMNS.length + 1} className={`${td} text-muted text-center py-10`}>
                  {empty}
                </td>
              </tr>
            ) : (
              items.map((c) => (
                <tr key={c.id} className="border-b border-border-subtle last:border-b-0">
                  <td className={td}>
                    <div className="flex flex-col gap-0.5">
                      <span className="font-mono text-md max-w-[240px] truncate" title={c.id}>
                        {c.id}
                      </span>
                      <span className="text-sm text-muted">{formatWhen(c.uploaded_at)}</span>
                    </div>
                  </td>
                  <td className={`${td} whitespace-nowrap`}>{c.agent ?? <span className="text-muted">—</span>}</td>
                  <td className={td}>
                    <OutcomeBadge outcome={c.outcome} />
                  </td>
                  <td className={`${td} font-mono text-md`}>{formatDuration(c.duration_s)}</td>
                  <td className={td}>
                    <ScoreColumn call={c} />
                  </td>
                  <td className={td}>
                    <FlagsChip count={c.status === 'done' ? c.flag_count : 0} />
                  </td>
                  <td className={`${td} whitespace-nowrap`}>
                    {c.weakest_category ? categoryLabel(c.weakest_category) : <span className="text-muted">—</span>}
                  </td>
                  <td className={`${td} text-right`}>
                    <Link to={`/calls/${encodeURIComponent(c.id)}`} className="font-medium" aria-label={`Open call ${c.id}`}>
                      Open
                    </Link>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex justify-between items-center gap-3 px-[18px] py-3 border-t border-border text-md text-muted">
        <span>
          Showing {items.length} of {total}
        </span>
        <div className="flex gap-1.5">
          <button type="button" className={`${btnSecondary} text-md font-normal px-3`} disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Prev
          </button>
          <button type="button" className={`${btnSecondary} text-md font-normal px-3`} disabled={page >= lastPage} onClick={() => onPage(page + 1)}>
            Next
          </button>
        </div>
      </div>
    </section>
  )
}
