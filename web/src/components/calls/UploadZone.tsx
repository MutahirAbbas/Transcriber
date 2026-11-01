import { useEffect, useRef, useState, type DragEvent } from 'react'

import type { Agent } from '../../api/types'
import { audioFromDrop } from '../../lib/upload'
import { UploadIcon } from '../Icons'

interface Props {
  agents: Agent[]
  agent: string
  onAgentChange: (agentId: string) => void
  onFiles: (files: File[]) => void
  onBrowse: () => void
  progress: { done: number; total: number } | null
  notice: string | null
  error: string | null
}

const hasFiles = (e: { dataTransfer: DataTransfer | null }) => !!e.dataTransfer?.types.includes('Files')

export function UploadZone({ agents, agent, onAgentChange, onFiles, onBrowse, progress, notice, error }: Props) {
  const [dragging, setDragging] = useState(false)
  const depth = useRef(0) // dragenter/leave fire for every child

  // a file dropped just outside the zone would otherwise navigate away to it
  useEffect(() => {
    const block = (e: globalThis.DragEvent) => {
      if (hasFiles(e)) e.preventDefault()
    }
    window.addEventListener('dragover', block)
    window.addEventListener('drop', block)
    return () => {
      window.removeEventListener('dragover', block)
      window.removeEventListener('drop', block)
    }
  }, [])

  const onDragEnter = (e: DragEvent) => {
    if (!hasFiles(e)) return
    e.preventDefault()
    depth.current += 1
    setDragging(true)
  }
  const onDragLeave = () => {
    depth.current = Math.max(0, depth.current - 1)
    if (depth.current === 0) setDragging(false)
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    depth.current = 0
    setDragging(false)
    void audioFromDrop(e.dataTransfer).then(onFiles)
  }

  return (
    <section aria-label="Upload" className="flex flex-col gap-2">
      <div
        onDragEnter={onDragEnter}
        onDragOver={(e) => hasFiles(e) && e.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`border border-dashed rounded-card px-5 py-4 md:px-6 md:py-5 flex flex-wrap items-center gap-x-6 gap-y-4 transition-colors ${
          dragging ? 'border-link bg-link-wash' : 'border-wave bg-faint'
        }`}
      >
        <button
          type="button"
          onClick={onBrowse}
          className="flex flex-[1_1_380px] min-w-0 items-center gap-4 md:gap-5 text-left cursor-pointer rounded-control"
        >
          <span className="w-11 h-11 rounded-control bg-surface border border-border text-ink flex items-center justify-center shrink-0">
            <UploadIcon size={20} />
          </span>
          <span className="flex flex-col gap-0.5 min-w-0">
            <span className="font-medium text-[16px]">
              {progress ? `${progress.done} of ${progress.total} uploaded` : 'Drop one recording or a whole folder'}
            </span>
            <span className="text-sm text-muted">
              WAV, MP3, M4A · up to 2 h each · stereo files give exact agent/customer separation · bulk uploads queue automatically
            </span>
          </span>
        </button>
        <div className="flex flex-col gap-1.5 flex-[1_1_200px] md:flex-[0_0_220px]">
          <label htmlFor="agent-select" className="text-md text-muted">
            Assign to agent
          </label>
          <select
            id="agent-select"
            value={agent}
            onChange={(e) => onAgentChange(e.target.value)}
            className="min-h-tap px-3 rounded-control border border-border bg-surface text-ink"
          >
            <option value="">Detect from filename</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      {notice && <p className="m-0 text-muted text-md">{notice}</p>}
      {error && (
        <p role="alert" className="m-0 text-rust text-md">
          Upload failed: {error}
        </p>
      )}
    </section>
  )
}
