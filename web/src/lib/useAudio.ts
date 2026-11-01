import { useCallback, useEffect, useRef, useState } from 'react'

/** The one <audio> on the page, and everything else that needs to read or move it. */
export function useAudio(fallbackDuration: number) {
  const ref = useRef<HTMLAudioElement>(null)
  const [time, setTime] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [loaded, setLoaded] = useState<number | null>(null)
  const [rate, setRateState] = useState(1)

  useEffect(() => {
    const a = ref.current
    if (!a) return
    const onTime = () => setTime(a.currentTime)
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onMeta = () => setLoaded(Number.isFinite(a.duration) ? a.duration : null)
    a.addEventListener('timeupdate', onTime)
    a.addEventListener('seeked', onTime)
    a.addEventListener('play', onPlay)
    a.addEventListener('pause', onPause)
    a.addEventListener('loadedmetadata', onMeta)
    return () => {
      a.removeEventListener('timeupdate', onTime)
      a.removeEventListener('seeked', onTime)
      a.removeEventListener('play', onPlay)
      a.removeEventListener('pause', onPause)
      a.removeEventListener('loadedmetadata', onMeta)
    }
  })

  const seek = useCallback((seconds: number, play = false) => {
    const a = ref.current
    if (!a) return
    a.currentTime = seconds
    setTime(seconds)
    if (play) void a.play().catch(() => {}) // autoplay may be refused; the seek still happened
  }, [])

  const toggle = useCallback(() => {
    const a = ref.current
    if (!a) return
    if (a.paused) void a.play().catch(() => {})
    else a.pause()
  }, [])

  const setRate = useCallback((r: number) => {
    if (ref.current) ref.current.playbackRate = r
    setRateState(r)
  }, [])

  return { ref, time, playing, duration: loaded ?? fallbackDuration, rate, seek, toggle, setRate }
}
