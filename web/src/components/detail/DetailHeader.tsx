import type { RefObject } from 'react'
import { useNavigate } from 'react-router-dom'

import { audioUrl, transcriptUrl } from '../../api/client'
import { isActive, useDeleteCall, useRescore } from '../../api/hooks'
import type { CallDetail } from '../../api/types'
import { formatDuration, formatWhen, toneOf100, toneStroke } from '../../lib/format'
import { btnSecondary, card } from '../../lib/ui'
import { OutcomeBadge } from '../Badges'

const R = 36
const CIRCUMFERENCE = 226 // ≈ 2π × 36

function Ring({ score, label }: { score: number | null; label: string }) {
  return (
    <div className="relative w-[84px] h-[84px] shrink-0">
      <svg aria-hidden="true" width="84" height="84" viewBox="0 0 84 84">
        <circle cx="42" cy="42" r={R} fill="none" strokeWidth="8" className="stroke-border-subtle" />
        {score != null && (
          <circle
            cx="42" cy="42" r={R} fill="none" strokeWidth="8" strokeLinecap="round"
            className={toneStroke[toneOf100(score)]}
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={CIRCUMFERENCE * (1 - Math.min(100, Math.max(0, score)) / 100)}
            transform="rotate(-90 42 42)"
          />
        )}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        {score != null ? (
          <>
            <span className="text-h1 font-semibold tracking-tightest leading-none">{score}</span>
            <span className="text-2xs text-muted uppercase tracking-label mt-[3px]">Score</span>
          </>
        ) : (
          <span className="text-xs text-muted px-2 leading-tight">{label}</span>
        )}
      </div>
    </div>
  )
}

// 40px as designed on desktop; 44px touch targets on phones
const headerBtn = `${btnSecondary} flex-1 min-h-tap md:min-h-10 px-1 text-md whitespace-nowrap`

const STAGE: Record<string, string> = {
  queued: 'Queued…',
  transcribing: 'Transcribing…',
  scoring: 'Scoring…',
  error: 'Error',
}

interface Props {
  call: CallDetail
  audioRef: RefObject<HTMLAudioElement>
}

export function DetailHeader({ call, audioRef }: Props) {
  const navigate = useNavigate()
  const rescore = useRescore(call.id)
  const remove = useDeleteCall()
  const done = call.status === 'done'
  const scoring = rescore.isPending || call.status === 'scoring'
  const meta = [formatWhen(call.uploaded_at, new Date(), ' '), formatDuration(call.duration_s)]

  const onDelete = () => {
    if (!window.confirm('Delete this call and its recording? This cannot be undone.')) return
    remove.mutate(call.id, { onSuccess: () => navigate('/') })
  }

  return (
    <header className={`${card} px-5 py-5 md:px-[26px] md:py-[22px] flex flex-wrap items-center gap-7`}>
      <div className="flex flex-col items-start sm:flex-row sm:items-center gap-[18px] flex-[1_1_360px] min-w-0">
        <Ring score={done ? call.overall_score : null} label={STAGE[call.status] ?? ''} />
        <div className="flex flex-col gap-1.5 min-w-0">
          <div className="flex items-center gap-2.5 flex-wrap">
            <h1 className="m-0 text-h1d font-semibold tracking-snug">{call.agent ?? 'Unassigned agent'}</h1>
            {done && <OutcomeBadge outcome={call.outcome} />}
          </div>
          <div className="font-mono text-sm text-muted">
            {/* only the long id may break mid-word */}
            <span className="break-all">{call.id}</span>
            {meta.map((part) => (
              <span key={part} className="whitespace-nowrap">
                {' · '}
                {part}
              </span>
            ))}
          </div>
          {call.status === 'error' && (
            <p role="alert" className="m-0 text-red">
              {call.error ?? 'Processing failed'}
            </p>
          )}
          {done && call.score?.summary && <p className="mt-0.5 mb-0 text-text-2 max-w-[560px]">{call.score.summary}</p>}
        </div>
      </div>

      <div className="flex flex-col gap-2 flex-[1_1_260px] md:flex-[0_1_260px] min-w-[220px]">
        <audio ref={audioRef} controls preload="metadata" aria-label="Call recording" src={audioUrl(call.id)} className="w-full" />
        <div className="flex gap-2">
          {call.transcript ? (
            <a href={transcriptUrl(call.id)} download className={headerBtn}>
              Download transcript
            </a>
          ) : (
            <button type="button" disabled className={headerBtn}>
              Download transcript
            </button>
          )}
          <button
            type="button"
            className={headerBtn}
            disabled={!call.transcript || scoring || isActive(call)}
            onClick={() => rescore.mutate()}
          >
            {scoring ? 'Scoring…' : 'Re-score'}
          </button>
        </div>
        <button
          type="button"
          onClick={onDelete}
          disabled={remove.isPending}
          className="self-end text-sm text-muted hover:text-red min-h-tap md:min-h-8 px-1 cursor-pointer"
        >
          Delete call
        </button>
        {(rescore.isError || remove.isError) && (
          <p role="alert" className="m-0 text-sm text-red">
            {(rescore.error ?? remove.error)?.message}
          </p>
        )}
      </div>
    </header>
  )
}
