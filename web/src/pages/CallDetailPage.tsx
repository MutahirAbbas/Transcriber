import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError, audioUrl } from '../api/client'
import { useCall, useDeleteCall, useRescore } from '../api/hooks'
import type { CallDetail, Finding } from '../api/types'
import { Bar } from '../components/Badges'
import { Findings } from '../components/detail/Findings'
import { Player } from '../components/detail/Player'
import { Transcript, type TranscriptHandle } from '../components/detail/Transcript'
import { BackIcon, WarnIcon } from '../components/Icons'
import { agentNameOf, feedbackText } from '../lib/feedback'
import { categoryLabel, formatDuration, formatWhen, modelShort, money, outcomeLabel } from '../lib/format'
import { isInProgress, stageText } from '../lib/stage'
import { buildTurns, findingTime, phaseSpans } from '../lib/transcript'
import { btnPrimary, btnSecondary, card } from '../lib/ui'
import { useAudio } from '../lib/useAudio'
import { useTitle } from '../lib/useTitle'

const LOW_SCORE = 5 // a category bar goes rust below this

function TopBar({ call }: { call: CallDetail | null }) {
  const nav = 'text-muted hover:text-ink min-h-tap md:min-h-0 inline-flex items-center'
  const off = 'text-muted opacity-40 min-h-tap md:min-h-0 inline-flex items-center'
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-1 text-md">
      <Link to="/" className="inline-flex items-center gap-1.5 text-muted hover:text-ink min-h-tap md:min-h-0">
        <BackIcon />
        All calls
      </Link>
      {call && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted min-w-0">
          <span className="font-mono truncate max-w-full" title={call.id}>{call.id}</span>
          <span>{formatWhen(call.uploaded_at, new Date(), ' ')}</span>
          <span>
            {formatDuration(call.duration_s)}
            {call.stereo != null && ` · ${call.stereo ? 'stereo' : 'mono'}`}
          </span>
          {call.prev_id ? <Link to={`/calls/${encodeURIComponent(call.prev_id)}`} className={nav}>Previous</Link> : <span className={off}>Previous</span>}
          {call.next_id ? <Link to={`/calls/${encodeURIComponent(call.next_id)}`} className={nav}>Next</Link> : <span className={off}>Next</span>}
        </div>
      )}
    </div>
  )
}

function Skeleton({ className = '', lines }: { className?: string; lines: number }) {
  return (
    <div aria-hidden="true" className={`${card} p-5 flex flex-col gap-3 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className={`h-3 rounded-bar bg-divider motion-safe:animate-pulse ${i % 3 === 2 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

function CopyFeedback({ call, className, onCopied }: { call: CallDetail; className: string; onCopied: (ok: boolean) => void }) {
  const agent = agentNameOf(call)
  return (
    <button
      type="button"
      className={className}
      onClick={() => {
        navigator.clipboard.writeText(feedbackText(call)).then(() => onCopied(true), () => onCopied(false))
      }}
    >
      {agent ? `Copy feedback for ${agent}` : 'Copy feedback'}
    </button>
  )
}

function ScoreLine({ call }: { call: CallDetail }) {
  const coaching = (call.findings ?? []).filter((f) => f.kind === 'coach').length
  const parts = [
    `${call.flag_count} ${call.flag_count === 1 ? 'flag' : 'flags'}`,
    `${coaching} coachable ${coaching === 1 ? 'moment' : 'moments'}`,
  ]
  if (call.agent && call.agent_average != null && call.overall_score != null) {
    const side = call.overall_score === call.agent_average ? 'at' : call.overall_score > call.agent_average ? 'above' : 'below'
    parts.push(`${side} ${call.agent.name}’s ${call.agent_average} average`)
  }
  return <p className="m-0 text-muted">{parts.join(' · ')}</p>
}

function CategoryTiles({ call }: { call: CallDetail }) {
  const { categories, labels } = call.scorecard
  const scores = call.score?.scores ?? {}
  const values = categories.flatMap((c) => (scores[c] ? [scores[c]!.score] : []))
  const lowest = values.length ? Math.min(...values) : null
  return (
    <ul className="m-0 p-0 list-none grid grid-cols-2 min-[440px]:grid-cols-3 xl:grid-cols-6 gap-2">
      {categories.map((c) => {
        const entry = scores[c]
        if (!entry) return null
        // the design only tints Compliance, and only when nothing's lower
        const tinted = c === 'compliance' && entry.score === lowest
        return (
          <li
            key={c}
            title={entry.evidence}
            className={`rounded-tile border px-4 py-3 flex flex-col gap-1.5 min-w-0 ${tinted ? 'bg-rust-wash border-rust-edge' : 'bg-surface border-border'}`}
          >
            <span className={`text-md truncate ${tinted ? 'text-rust-ink' : 'text-muted'}`}>{labels[c] ?? categoryLabel(c)}</span>
            <span className={`text-[26px] leading-none font-medium tracking-snug ${tinted ? 'text-rust' : ''}`}>
              {entry.score}
              <span className={`text-sm font-normal tracking-normal ${tinted ? 'text-rust-ink' : 'text-muted'}`}>/10</span>
            </span>
            <Bar value={entry.score / 10} className={entry.score < LOW_SCORE ? 'bg-rust' : 'bg-muted'} />
          </li>
        )
      })}
    </ul>
  )
}

function OutcomePill({ call }: { call: CallDetail }) {
  if (!call.outcome) return null
  if (call.outcome === 'sale') {
    const bits = ['Sale']
    if (call.sale?.coverage_usd != null) bits.push(money(call.sale.coverage_usd))
    if (call.sale?.premium_monthly_usd != null) bits.push(`${money(call.sale.premium_monthly_usd)}/mo`)
    return <span className="px-3 py-1 rounded-full bg-link-tint text-link-ink text-md whitespace-nowrap">{bits.join(' · ')}</span>
  }
  return <span className="px-3 py-1 rounded-full bg-pill text-muted text-md border border-border whitespace-nowrap">{outcomeLabel(call.outcome)}</span>
}

function Who({ call }: { call: CallDetail }) {
  const agent = agentNameOf(call)
  const c = call.customer
  const name = [c?.first_name, c?.last_name].filter(Boolean).join(' ')
  const customer = [name, c?.age, c?.state].filter((v) => v != null && v !== '').join(', ')
  return (
    <h1 className="m-0 text-[26px] md:text-h2 font-medium tracking-snug leading-tight">
      {agent ?? 'Call'}
      {customer && (
        <>
          <span className="text-wave font-normal"> / </span>
          <span className="text-muted">{customer}</span>
        </>
      )}
    </h1>
  )
}

function Header({ call, actions }: { call: CallDetail; actions: React.ReactNode }) {
  const target = call.scorecard.target_score
  const score = call.overall_score
  const working = isInProgress(call)
  return (
    <div className="flex flex-col lg:flex-row gap-6 lg:gap-10">
      <div className="lg:w-[260px] shrink-0 flex flex-col gap-3">
        {working || score == null ? (
          <div className="flex flex-col gap-2 py-3">
            <span className="text-h3 font-medium tracking-snug">{call.status === 'error' ? 'Couldn’t finish' : stageText(call)}</span>
            {call.status === 'error' && <p className="m-0 text-rust break-words">{stageText(call)}</p>}
          </div>
        ) : (
          <>
            <div className="flex items-baseline gap-2">
              <span className={`text-score font-medium tracking-tightest ${score < target ? 'text-rust' : 'text-ink'}`}>{score}</span>
              <span className="text-[20px] text-muted">/ 100</span>
            </div>
            {call.critical_count > 0 && (
              <p className="m-0 flex items-center gap-2 text-rust-ink font-medium text-[16px]">
                <WarnIcon size={18} className="text-rust" />
                Review for compliance
              </p>
            )}
            <ScoreLine call={call} />
          </>
        )}
        <div className="flex flex-col gap-2 pt-2">{actions}</div>
      </div>
      <div className="flex-1 min-w-0 flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Who call={call} />
          <OutcomePill call={call} />
        </div>
        {call.score?.summary && !working && <p className="m-0 text-lg leading-relaxed text-text-2 max-w-[880px]">{call.score.summary}</p>}
        {!call.scored_v2 && call.score && !working && (
          <p className="m-0 text-md text-rust-ink">Scored with the old scorecard. Re-score to see phases, findings and work on next.</p>
        )}
        {!working && <CategoryTiles call={call} />}
      </div>
    </div>
  )
}

function WorkOnNext({ call, actions }: { call: CallDetail; actions: React.ReactNode }) {
  const work = call.score?.work_on_next
  if (!work) return null
  return (
    <section aria-label="Work on next" className="bg-band text-band-text rounded-card p-6 md:p-8 flex flex-col lg:flex-row gap-6 lg:gap-10">
      <div className="lg:w-[260px] shrink-0 flex flex-col gap-3">
        <h2 className="m-0 text-h3 font-medium tracking-tight text-surface">Work on next</h2>
        <p className="m-0 text-band-muted">{work.intro}</p>
        <div className="flex flex-wrap gap-2 pt-2">{actions}</div>
      </div>
      <ol className="m-0 p-0 list-none flex-1 grid md:grid-cols-2 xl:grid-cols-3 gap-x-8 gap-y-5 content-start">
        {work.items.map((item, i) => (
          <li key={i} className="flex gap-4">
            <span className="font-mono text-md text-band-muted pt-0.5">{String(i + 1).padStart(2, '0')}</span>
            <span>{item}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

function FooterLine({ call, onDelete }: { call: CallDetail; onDelete: () => void }) {
  const scorer = call.score?._meta?.scorer_model
  const parts = [
    scorer ? `Scored by Claude ${modelShort(scorer)}` : null,
    call.scored_v2 ? `scorecard v${call.scorer_version?.split('.')[0] ?? call.scorecard.version}` : null,
    call.model ? `transcribed with ${call.model}${call.worker_id ? ` on ${call.worker_id}` : ''}` : null,
    `speakers ${call.speaker_source ?? 'none'}`,
    call.rtf != null ? `${call.rtf.toFixed(2)}× real time` : null,
  ].filter(Boolean)
  return (
    <p className="m-0 text-sm text-muted">
      {parts.join(' · ')}
      {parts.length > 0 && ' · '}
      <button type="button" onClick={onDelete} className="underline cursor-pointer hover:text-ink min-h-tap md:min-h-0">
        Delete call
      </button>
    </p>
  )
}

export function CallDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { data: call, error, isLoading } = useCall(id)
  const rescore = useRescore(id)
  const del = useDeleteCall()
  const audio = useAudio(call?.duration_s ?? 0)
  const transcript = useRef<TranscriptHandle>(null)
  const [toast, setToast] = useState<string | null>(null)
  useTitle(call ? (agentNameOf(call) ?? 'Call') : '')

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2000)
    return () => clearTimeout(t)
  }, [toast])

  const duration = audio.duration
  const spans = useMemo(() => phaseSpans(call?.phases ?? null, call?.scorecard.phases ?? [], duration), [call?.phases, call?.scorecard.phases, duration])
  const turns = useMemo(
    () => buildTurns(call?.transcript?.segments ?? [], call?.speakers ?? {}, call?.score?.agent_speaker ?? null, call?.findings ?? null, spans),
    [call?.transcript, call?.speakers, call?.score?.agent_speaker, call?.findings, spans],
  )

  const onFinding = useCallback((f: Finding) => {
    audio.seek(findingTime(f))
    transcript.current?.scrollToTime(findingTime(f))
  }, [audio.seek])
  const playFrom = useCallback((s: number) => audio.seek(s, true), [audio.seek])

  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <TopBar call={null} />
        <Skeleton lines={5} />
      </div>
    )
  }
  if (error || !call) {
    const notFound = error instanceof ApiError && error.status === 404
    return (
      <div className="flex flex-col gap-6">
        <TopBar call={null} />
        <p className="m-0 text-muted">{notFound ? 'This call no longer exists.' : `Couldn't load the call: ${error?.message}`}</p>
      </div>
    )
  }

  const working = isInProgress(call)
  const onCopied = (ok: boolean) => setToast(ok ? 'Copied' : 'Couldn’t copy. Check clipboard permissions.')
  const remove = () => {
    if (window.confirm('Delete this call, its audio and its transcript? This can’t be undone.')) {
      del.mutate(call.id, { onSuccess: () => navigate('/') })
    }
  }
  const canRescore = !!call.transcript && !working
  const rescoreBtn = (cls: string) => (
    <button type="button" className={cls} disabled={!canRescore || rescore.isPending} onClick={() => rescore.mutate()}>
      {rescore.isPending ? 'Starting…' : 'Re-score'}
    </button>
  )

  return (
    <div className="flex flex-col gap-6 md:gap-8">
      <TopBar call={call} />
      <Header
        call={call}
        actions={
          <>
            {call.score && !working && <CopyFeedback call={call} className={`${btnPrimary} w-full`} onCopied={onCopied} />}
            {rescoreBtn(`${btnSecondary} w-full`)}
            {rescore.isError && <p role="alert" className="m-0 text-sm text-rust">{rescore.error.message}</p>}
          </>
        }
      />
      {working ? (
        <>
          <Skeleton lines={3} />
          <div className="flex flex-col lg:flex-row gap-6">
            <Skeleton className="lg:w-[380px] shrink-0" lines={8} />
            <Skeleton className="flex-1" lines={12} />
          </div>
        </>
      ) : (
        <>
          <Player audio={audio} peaks={call.waveform ?? []} findings={call.findings ?? []} spans={spans} scores={call.score?.scores ?? {}} />
          {/* Findings first when stacked: after an hour-long transcript they'd be far down the page */}
          <div className="flex flex-col lg:flex-row gap-6">
            <div className={`${card} lg:w-[380px] shrink-0 h-[560px] lg:h-[752px] flex flex-col overflow-hidden`}>
              <Findings findings={call.findings} labels={call.scorecard.labels} onSelect={onFinding} className="flex-1" />
            </div>
            <div className={`${card} lg:flex-1 min-w-0 h-[640px] lg:h-[752px] flex flex-col overflow-hidden`}>
              <Transcript
                ref={transcript}
                callId={call.id}
                turns={turns}
                spans={spans}
                time={audio.time}
                playing={audio.playing}
                onTime={playFrom}
                className="flex-1"
              />
            </div>
          </div>
          <WorkOnNext
            call={call}
            actions={
              <>
                <CopyFeedback call={call} className={`${btnSecondary} !border-surface !text-ink`} onCopied={onCopied} />
                {rescoreBtn(`${btn2}`)}
              </>
            }
          />
        </>
      )}
      <FooterLine call={call} onDelete={remove} />
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- call recordings have no captions; the transcript is alongside */}
      <audio ref={audio.ref} src={audioUrl(call.id)} preload="metadata" className="hidden" />
      <div role="status" aria-live="polite" className="fixed bottom-6 left-1/2 -translate-x-1/2 z-30 pointer-events-none">
        {toast && <span className="px-4 py-2.5 rounded-control bg-ink text-surface text-md shadow-[0_8px_24px_-12px_theme(colors.muted)]">{toast}</span>}
      </div>
    </div>
  )
}

const btn2 =
  'inline-flex items-center justify-center min-h-tap px-5 rounded-control border border-band-edge text-surface font-medium cursor-pointer hover:bg-text-2 disabled:opacity-60 disabled:cursor-not-allowed'
