import { useCallback, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { isActive, useCall } from '../api/hooks'
import { AnalysisPanel } from '../components/detail/AnalysisPanel'
import { DetailHeader } from '../components/detail/DetailHeader'
import { TranscriptPanel } from '../components/detail/TranscriptPanel'
import { BackIcon } from '../components/Icons'
import { card } from '../lib/ui'

const TRANSCRIPT_COL = 'xl:flex-[999_1_520px] min-w-0'
const ANALYSIS_COL = 'order-first xl:order-none xl:flex-[1_1_360px] min-w-0 xl:max-w-[460px]'

function Skeleton({ className, lines }: { className: string; lines: number }) {
  return (
    <div aria-hidden="true" className={`${card} p-5 flex flex-col gap-3 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className={`h-3 rounded-bar bg-border-subtle animate-pulse ${i % 3 === 2 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

export function CallDetailPage() {
  const { id = '' } = useParams()
  const { data: call, error, isLoading } = useCall(id)
  const audioRef = useRef<HTMLAudioElement>(null)

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = seconds
    void audio.play().catch(() => {}) // autoplay may be refused; the seek still happened
  }, [])

  const back = (
    <Link to="/" className="inline-flex items-center gap-1.5 text-muted hover:text-ink text-md min-h-8 self-start">
      <BackIcon />
      All calls
    </Link>
  )

  if (isLoading) return <div className="flex flex-col gap-6">{back}<Skeleton className="" lines={4} /></div>
  if (error || !call) {
    const notFound = error instanceof ApiError && error.status === 404
    return (
      <div className="flex flex-col gap-6">
        {back}
        <p className="m-0 text-muted">{notFound ? 'This call no longer exists.' : `Couldn't load the call: ${error?.message}`}</p>
      </div>
    )
  }

  const working = isActive(call)
  return (
    <div className="flex flex-col gap-6">
      {back}
      <DetailHeader call={call} audioRef={audioRef} />
      {/* Side by side from xl. Stacked, the analysis goes first: after an hour-long
          transcript it would be thousands of pixels down. */}
      <div className="flex flex-col xl:flex-row gap-5 xl:items-start">
        {working ? (
          <>
            <Skeleton className={TRANSCRIPT_COL} lines={9} />
            <Skeleton className={ANALYSIS_COL} lines={6} />
          </>
        ) : (
          <>
            <TranscriptPanel call={call} onSeek={seek} className={TRANSCRIPT_COL} />
            <AnalysisPanel call={call} className={ANALYSIS_COL} />
          </>
        )}
      </div>
    </div>
  )
}
