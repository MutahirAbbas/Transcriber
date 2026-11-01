import { forwardRef, memo, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'

import { transcriptUrl } from '../../api/client'
import { formatTimestamp } from '../../lib/format'
import { buildRows, matchRanges, spanRange, turnAt, type PhaseSpan, type Row, type Turn } from '../../lib/transcript'
import { btnSecondary, input } from '../../lib/ui'
import { SearchIcon } from '../Icons'

export interface TranscriptHandle {
  scrollToTime: (seconds: number) => void
}

function Highlighted({ text, query, current }: { text: string; query: string; current: number | null }) {
  const ranges = matchRanges(text, query)
  if (!ranges.length) return <>{text}</>
  const parts: React.ReactNode[] = []
  let at = 0
  ranges.forEach(([from, to], i) => {
    parts.push(text.slice(at, from))
    parts.push(
      <mark key={from} className={i === current ? 'bg-phase-deep text-ink rounded-bar' : 'bg-phase-light text-ink rounded-bar'}>
        {text.slice(from, to)}
      </mark>,
    )
    at = to
  })
  parts.push(text.slice(at))
  return <>{parts}</>
}

// Memoised: an hour-long call has well over a thousand turns, and the clock ticks four times a second.
const TurnRow = memo(function TurnRow({ turn, index, active, query, current, onTime }: {
  turn: Turn
  index: number
  active: boolean
  query: string
  current: number | null
  onTime: (s: number) => void
}) {
  const coach = turn.coach
  return (
    <li
      data-turn={index}
      className={`flex gap-3 md:gap-5 px-3 md:px-4 py-2.5 rounded-tile ${
        coach ? 'bg-link-wash' : active ? 'bg-faint shadow-[inset_2px_0_0_theme(colors.ink)]' : ''
      }`}
    >
      <button
        type="button"
        onClick={() => onTime(turn.start)}
        aria-label={`Play from ${formatTimestamp(turn.start)}`}
        className={`font-mono text-md shrink-0 w-[52px] text-left self-start pt-px cursor-pointer hover:underline ${
          coach ? 'text-link' : 'text-muted'
        }`}
      >
        {formatTimestamp(turn.start)}
      </button>
      <div className={`min-w-0 flex flex-col gap-0.5 ${turn.customer ? 'pl-5 md:pl-10' : ''}`}>
        <span className="text-md min-h-5">
          <span className={`font-medium ${turn.customer ? 'text-muted' : 'text-ink'}`}>{turn.name ?? ''}</span>
          {coach && <span className="text-link-ink"> · coachable</span>}
          {turn.flag && <span className="text-rust"> · flag</span>}
        </span>
        <p className={`m-0 text-lg leading-snug break-words ${turn.customer ? 'text-muted' : 'text-text-2'}`}>
          <Highlighted text={turn.text} query={query} current={current} />
        </p>
      </div>
    </li>
  )
})

function Divider({ span }: { span: PhaseSpan }) {
  return (
    <li className="flex items-center gap-3 px-3 md:px-4 pt-4 pb-2 text-sm text-muted" aria-label={`${span.label}, ${spanRange(span)}`}>
      <span className="uppercase tracking-head font-medium">{span.label}</span>
      <span aria-hidden="true" className="flex-1 h-px bg-divider" />
      <span className="font-mono">{spanRange(span)}</span>
    </li>
  )
}

export const Transcript = forwardRef<TranscriptHandle, {
  callId: string
  turns: Turn[]
  spans: PhaseSpan[]
  time: number
  playing: boolean
  onTime: (s: number) => void
  className: string
}>(function Transcript({ callId, turns, spans, time, playing, onTime, className }, ref) {
  const [query, setQuery] = useState('')
  const [follow, setFollow] = useState(true)
  const [hit, setHit] = useState(0)
  const scroller = useRef<HTMLOListElement>(null)
  const rows: Row[] = useMemo(() => buildRows(turns, spans), [turns, spans])

  // Every match in the transcript, in order, as [turn, nth match within that turn].
  const hits = useMemo(
    () => turns.flatMap((t, i) => matchRanges(t.text, query).map((_, n) => [i, n] as const)),
    [turns, query],
  )
  useEffect(() => setHit(0), [query])

  /** Inside the card only: scrollIntoView would drag the whole page along. */
  const scrollToTurn = (index: number) => {
    const box = scroller.current
    const el = box?.querySelector<HTMLElement>(`[data-turn="${index}"]`)
    if (box && el) box.scrollTop = el.offsetTop - 16   // the list is its turns' offset parent
  }

  useImperativeHandle(ref, () => ({ scrollToTime: (s) => scrollToTurn(turnAt(turns, s)) }), [turns])

  const active = turnAt(turns, time)
  useEffect(() => {
    if (follow && playing && active >= 0) scrollToTurn(active)
  }, [active, follow, playing])

  const next = () => {
    if (!hits.length) return
    const n = (hit + 1) % hits.length
    setHit(n)
    scrollToTurn(hits[n]![0])
  }
  const [hitTurn, hitN] = hits[hit] ?? [null, null]

  return (
    <section aria-label="Transcript" className={`flex flex-col min-h-0 ${className}`}>
      <header className="flex flex-wrap items-center gap-2 px-5 py-3.5 border-b border-divider">
        <h2 className="m-0 text-lg font-medium mr-auto">Transcript</h2>
        <label className={`${input} flex items-center gap-2 flex-[1_1_180px] md:flex-[0_1_290px] min-w-0 text-muted`}>
          <SearchIcon />
          <span className="sr-only">Find a phrase</span>
          <input
            type="search"
            value={query}
            placeholder="Find a phrase"
            onChange={(e) => {
              setQuery(e.target.value)
              const first = turns.findIndex((t) => matchRanges(t.text, e.target.value).length)
              if (first >= 0) scrollToTurn(first)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                next()
              }
            }}
            className="border-0 outline-none flex-1 min-w-0 text-md text-ink bg-transparent placeholder:text-muted"
          />
          {query.trim() && (
            <span className="text-sm whitespace-nowrap" aria-live="polite">
              {hits.length ? `${hit + 1} of ${hits.length}` : 'No matches'}
            </span>
          )}
        </label>
        <button
          type="button"
          aria-pressed={follow}
          onClick={() => setFollow((f) => !f)}
          className={`${btnSecondary} !px-3.5 text-md ${follow ? '!bg-pill' : ''}`}
        >
          Follow audio
        </button>
        <a href={transcriptUrl(callId)} download className={`${btnSecondary} !px-3.5 text-md`}>
          Download
        </a>
      </header>
      <ol ref={scroller} className="relative m-0 p-2 list-none flex-1 min-h-0 overflow-y-auto">
        {!rows.length && <li className="px-3 py-8 text-muted text-md">No transcript yet.</li>}
        {rows.map((row) =>
          row.kind === 'divider' ? (
            <Divider key={`d-${row.span.phase}-${row.span.start}`} span={row.span} />
          ) : (
            <TurnRow
              key={row.index}
              turn={row.turn}
              index={row.index}
              active={follow && row.index === active && time > 0}
              query={query}
              current={row.index === hitTurn ? hitN : null}
              onTime={onTime}
            />
          ),
        )}
      </ol>
    </section>
  )
})
