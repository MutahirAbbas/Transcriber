import { useEffect, useRef, useState, type DragEvent } from 'react'

import type { Agent } from '../../api/types'
import { UploadIcon } from '../Icons'

interface Props {
  agents: Agent[]
  agent: string
  onAgentChange: (agentId: string) => void
  onFiles: (files: File[]) => void
  onBrowse: () => void
  uploading: number // files in flight
  error: string | null
}

const hasFiles = (e: { dataTransfer: DataTransfer | null }) => !!e.dataTransfer?.types.includes('Files')

export function UploadZone({ agents, agent, onAgentChange, onFiles, onBrowse, uploading, error }: Props) {
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
    onFiles(Array.from(e.dataTransfer.files))
  }

  return (
    <section aria-label="Upload" className="flex flex-col gap-2">
      <div
        onDragEnter={onDragEnter}
        onDragOver={(e) => hasFiles(e) && e.preventDefault()}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={`bg-surface border-[1.5px] border-dashed rounded-card px-5 py-5 md:px-8 md:py-7 flex flex-wrap items-center gap-6 transition-colors ${
          dragging ? 'border-accent bg-ground' : 'border-dropzone'
        }`}
      >
        <button
          type="button"
          onClick={onBrowse}
          className="flex flex-[1_1_380px] min-w-0 items-center gap-6 text-left cursor-pointer rounded-control"
        >
          <span className="w-12 h-12 rounded-card-sm bg-border-subtle text-ink flex items-center justify-center shrink-0">
            <UploadIcon />
          </span>
          <span className="flex flex-col gap-1 min-w-0">
            <span className="font-semibold text-h2">
              {uploading ? `Uploading ${uploading} ${uploading === 1 ? 'file' : 'files'}…` : 'Drop call recordings here, or choose files'}
            </span>
            <span className="text-muted">
              WAV, MP3, M4A · up to 2 hours each · stereo recordings get exact agent/customer separation
            </span>
          </span>
        </button>
        <div className="flex flex-col gap-2 flex-[1_1_240px] md:flex-[0_1_300px] min-w-[220px]">
          <label htmlFor="agent-select" className="text-sm text-muted font-medium">
            Assign to agent
          </label>
          <select
            id="agent-select"
            value={agent}
            onChange={(e) => onAgentChange(e.target.value)}
            className="min-h-tap px-3 rounded-control border border-control bg-surface text-ink"
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
      {error && (
        <p role="alert" className="m-0 text-red text-md">
          Upload failed: {error}
        </p>
      )}
    </section>
  )
}
