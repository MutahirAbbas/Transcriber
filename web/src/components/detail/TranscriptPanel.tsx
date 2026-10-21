import { memo, useMemo } from 'react'

import type { CallDetail, Segment } from '../../api/types'
import { categoryLabel, formatTimestamp } from '../../lib/format'
import { placeMoments, speakerLabel, type PlacedMoment } from '../../lib/moments'
import { card } from '../../lib/ui'

interface Props {
  call: CallDetail
  onSeek: (seconds: number) => void
  className: string
}

function Legend() {
  const swatch = 'w-2.5 h-2.5 rounded-swatch'
  return (
    <div className="flex flex-wrap gap-3.5 items-center text-sm text-muted">
      <span className="inline-flex items-center gap-1.5">
        <span className={`${swatch} bg-accent`} />
        Agent
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className={`${swatch} bg-customer`} />
        Customer
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className={`${swatch} bg-moment-swatch border border-amber`} />
        Coachable moment
      </span>
    </div>
  )
}

function Timestamp({ seconds, onSeek, tone }: { seconds: number; onSeek: (s: number) => void; tone: string }) {
  const ts = formatTimestamp(seconds)
  return (
    <button
      type="button"
      onClick={() => onSeek(seconds)}
      aria-label={`Play from ${ts}`}
      className={`font-mono text-sm w-11 shrink-0 pt-[3px] text-left self-start cursor-pointer hover:text-accent hover:underline ${tone}`}
    >
      {ts}
    </button>
  )
}

const momentText = (p: PlacedMoment) =>
  [p.moment.what_happened, p.moment.better_approach && `Better: ${p.moment.better_approach}`].filter(Boolean).join(' ')

interface TurnProps {
  seg: Segment
  label: string
  moments: PlacedMoment[] | undefined
  onSeek: (s: number) => void
}

const Turn = memo(function Turn({ seg, label, moments, onSeek }: TurnProps) {
  const labelTone = label === 'Agent' ? 'text-accent' : 'text-muted'
  const body = (
    <div className="flex flex-col gap-[3px] min-w-0">
      <span className={`text-sm font-semibold ${labelTone}`}>{label}</span>
      <p className="m-0 text-ink break-words">{seg.text}</p>
    </div>
  )
  if (!moments?.length) {
    return (
      <div className="flex gap-3.5 py-2.5">
        <Timestamp seconds={seg.start} onSeek={onSeek} tone="text-muted" />
        {body}
      </div>
    )
  }
  return (
    <div className="flex gap-3.5 px-3.5 py-3 my-1 -mx-3.5 bg-moment-bg border border-moment-border rounded-card-sm">
      <Timestamp seconds={seg.start} onSeek={onSeek} tone="text-moment-title" />
      <div className="flex flex-col gap-2 min-w-0">
        {body}
        {moments.map((p, i) => (
          <div key={i} className="border-t border-moment-border pt-2 flex flex-col gap-1">
            <span className="text-sm font-semibold text-moment-title">
              Coachable moment{p.category ? ` · ${categoryLabel(p.category)}` : ''}
            </span>
            <p className="m-0 text-md text-moment-body">{momentText(p)}</p>
          </div>
        ))}
      </div>
    </div>
  )
})

export function TranscriptPanel({ call, onSeek, className }: Props) {
  const segments = call.transcript?.segments ?? []
  const { bySegment, other } = useMemo(
    () => placeMoments(segments, call.score?.coachable_moments ?? [], call.scorecard.categories),
    [segments, call.score, call.scorecard.categories],
  )

  return (
    <section aria-label="Transcript" className={`${card} ${className} flex flex-col`}>
      <div className="flex flex-wrap items-center justify-between gap-2.5 px-5 py-3.5 border-b border-border">
        <h2 className="m-0 text-h2 font-semibold">Transcript</h2>
        <Legend />
      </div>
      <div className="px-5 pt-2 pb-5 flex flex-col gap-0.5">
        {segments.length === 0 && <p className="text-muted my-4">No speech was transcribed.</p>}
        {segments.map((seg, i) => (
          <Turn key={i} seg={seg} label={speakerLabel(seg.speaker, call.speaker_map)} moments={bySegment.get(i)} onSeek={onSeek} />
        ))}
        {other.length > 0 && (
          <div className="mt-4 pt-4 border-t border-border flex flex-col gap-2">
            <h3 className="m-0 text-md font-semibold text-moment-title">Other moments</h3>
            <ul className="m-0 pl-0 list-none flex flex-col gap-2">
              {other.map((p, i) => (
                <li key={i} className="flex gap-3.5 text-md">
                  <span className="font-mono text-sm text-muted w-11 shrink-0 pt-px">{p.moment.timestamp}</span>
                  <span className="text-moment-body">
                    {p.category && <strong className="font-semibold">{categoryLabel(p.category)}: </strong>}
                    {momentText(p)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}
