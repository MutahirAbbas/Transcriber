import { memo, useMemo } from 'react'

import type { Finding } from '../../api/types'
import { formatTimestamp, clamp01 } from '../../lib/format'
import { downsample, findingTime, phaseFill, type PhaseSpan } from '../../lib/transcript'
import { PauseIcon, PlayIcon } from '../Icons'

const SPEEDS = [0.75, 1, 1.25, 1.5, 2]
const BARS = 133
const KEY_STEP_S = 5
// under this share of the bar there's only room for the score (the "6" in the design)
const SCORE_ONLY_BELOW = 0.06

interface Audio {
  time: number
  playing: boolean
  duration: number
  rate: number
  seek: (s: number) => void
  toggle: () => void
  setRate: (r: number) => void
}

const Bars = memo(function Bars({ peaks }: { peaks: number[] }) {
  const bars = useMemo(() => downsample(peaks, BARS), [peaks])
  return (
    <div aria-hidden="true" className="absolute inset-0 flex items-center justify-between pointer-events-none">
      {bars.map((p, i) => (
        <span key={i} className="w-0.5 bg-wave rounded-bar" style={{ height: `${Math.max(10, p * 100)}%` }} />
      ))}
    </div>
  )
})

function Waveform({ audio, peaks, findings }: { audio: Audio; peaks: number[]; findings: Finding[] }) {
  const { duration, time, seek } = audio
  const at = (s: number) => `${clamp01(duration ? s / duration : 0) * 100}%`
  const markers = findings.filter((f) => f.kind === 'flag' || f.kind === 'coach')
  return (
    <div
      className="relative h-9 cursor-pointer"
      role="slider"
      tabIndex={0}
      aria-label="Seek"
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      aria-valuenow={Math.round(time)}
      aria-valuetext={formatTimestamp(time)}
      onClick={(e) => {
        const box = e.currentTarget.getBoundingClientRect()
        seek(clamp01((e.clientX - box.left) / box.width) * duration)
      }}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight') seek(Math.min(duration, time + KEY_STEP_S))
        else if (e.key === 'ArrowLeft') seek(Math.max(0, time - KEY_STEP_S))
      }}
    >
      <Bars peaks={peaks} />
      {markers.map((f, i) => (
        <button
          key={i}
          type="button"
          title={`${f.timestamp} · ${f.text}`}
          aria-label={`${f.kind === 'flag' ? 'Flag' : 'Coachable moment'} at ${f.timestamp}`}
          className="absolute inset-y-0 -translate-x-1/2 w-3 flex justify-center cursor-pointer group"
          style={{ left: at(findingTime(f)) }}
          onClick={(e) => {
            e.stopPropagation()
            seek(findingTime(f))
          }}
        >
          <span className={`w-[3px] h-full rounded-bar group-hover:w-1 ${f.kind === 'flag' ? 'bg-rust' : 'bg-link'}`} />
        </button>
      ))}
      <span aria-hidden="true" className="absolute inset-y-0 w-px bg-ink pointer-events-none" style={{ left: at(time) }} />
    </div>
  )
}

function PhaseBar({ spans, scores, seek, duration }: {
  spans: PhaseSpan[]
  scores: Record<string, { score: number }>
  seek: (s: number) => void
  duration: number
}) {
  if (!spans.length) return null
  return (
    <div className="overflow-x-auto">
      <ol className="m-0 p-0 list-none flex gap-1 min-w-[640px] md:min-w-0">
        {spans.map((s) => {
          const score = scores[s.category]?.score ?? null
          const share = duration ? (s.end - s.start) / duration : 0
          return (
            <li key={`${s.phase}-${s.start}`} className="min-w-0" style={{ flex: `${Math.max(share, 0.02)} 1 0%` }}>
              <button
                type="button"
                title={`${s.label}${score != null ? ` ${score}` : ''} · ${formatTimestamp(s.start)}`}
                aria-label={`${s.label}${score != null ? `, score ${score}` : ''}, starts ${formatTimestamp(s.start)}`}
                onClick={() => seek(s.start)}
                className={`w-full h-[30px] px-1 rounded-phase text-sm truncate cursor-pointer hover:opacity-80 ${phaseFill(score)}`}
              >
                {share < SCORE_ONLY_BELOW ? (score ?? '') : `${s.label}${score != null ? ` ${score}` : ''}`}
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function Player({ audio, peaks, findings, spans, scores }: {
  audio: Audio
  peaks: number[]
  findings: Finding[]
  spans: PhaseSpan[]
  scores: Record<string, { score: number }>
}) {
  return (
    <section aria-label="Player" className="py-6 border-y border-border flex flex-col gap-2.5">
      <div className="flex flex-wrap md:flex-nowrap items-center gap-x-4 gap-y-3">
        <button
          type="button"
          onClick={audio.toggle}
          aria-label={audio.playing ? 'Pause' : 'Play'}
          className="w-11 h-11 rounded-full bg-ink text-surface flex items-center justify-center shrink-0 cursor-pointer hover:opacity-90"
        >
          {audio.playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <span className="font-mono text-md whitespace-nowrap tabular-nums order-none">
          {formatTimestamp(audio.time)} <span className="text-muted">/</span> {formatTimestamp(audio.duration)}
        </span>
        <div className="order-last md:order-none basis-full md:basis-auto flex-1 min-w-0 flex flex-col gap-2">
          <Waveform audio={audio} peaks={peaks} findings={findings} />
          <PhaseBar spans={spans} scores={scores} seek={audio.seek} duration={audio.duration} />
        </div>
        <label className="ml-auto md:ml-0 shrink-0">
          <span className="sr-only">Playback speed</span>
          <select
            value={audio.rate}
            onChange={(e) => audio.setRate(Number(e.target.value))}
            className="min-h-tap pl-3 pr-1 rounded-control border border-border bg-surface text-ink font-mono text-sm"
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}×
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="m-0 md:pl-[60px] flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="w-[3px] h-3 rounded-bar bg-rust" /> Flag
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="w-[3px] h-3 rounded-bar bg-link" /> Coachable moment
        </span>
        <span>Phase bar shows where the call lost its score · click anything to jump</span>
      </p>
    </section>
  )
}
