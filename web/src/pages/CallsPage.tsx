import { useEffect, useRef, useState } from 'react'

import { csvUrl } from '../api/client'
import { useAgents, useUpload } from '../api/hooks'
import type { Filter } from '../api/types'
import { CallsTable } from '../components/calls/CallsTable'
import { StatsRow } from '../components/calls/StatsRow'
import { UploadZone } from '../components/calls/UploadZone'
import { ArrowUpIcon } from '../components/Icons'
import { keepAudio } from '../lib/upload'
import { btnPrimary, btnSecondary, card } from '../lib/ui'
import { useDebounced } from '../lib/useDebounced'
import { useTitle } from '../lib/useTitle'

export function CallsPage() {
  useTitle('Calls')
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const { data: agents = [] } = useAgents()
  const [agent, setAgent] = useState('')
  const upload = useUpload()
  const [notice, setNotice] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)

  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search, 300)
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [filter, q])

  /** Everything goes through here, so a picker can't slip a non-audio file past the drop rules. */
  const send = (picked: File[]) => {
    const files = keepAudio(picked)
    const skipped = picked.length - files.length
    setNotice(skipped ? `Skipped ${skipped} ${skipped === 1 ? 'file that isn’t' : 'files that aren’t'} WAV, MP3 or M4A.` : null)
    if (files.length) upload.mutate({ files, agent })
  }
  const pickFiles = () => {
    setPicking(false)
    fileInput.current?.click()
  }
  const pickFolder = () => {
    setPicking(false)
    folderInput.current?.click()
  }

  const onPicked = (e: React.ChangeEvent<HTMLInputElement>) => {
    send(Array.from(e.target.files ?? []))
    e.target.value = '' // so choosing the same file again still fires
  }

  return (
    <div className="flex flex-col gap-6 md:gap-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1 flex-[1_1_360px] max-w-[600px]">
          <h1 className="m-0 text-h2 font-medium tracking-snug">Calls</h1>
          <p className="m-0 text-muted">
            Every recording is transcribed on your Mac, scored against the scorecard, and ready for review in about a minute.
          </p>
        </div>
        <div className="flex gap-2.5 flex-wrap">
          <a href={csvUrl(filter, q)} download className={btnSecondary}>
            Export CSV
          </a>
          <div
            className="relative"
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget)) setPicking(false)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setPicking(false)
            }}
          >
            <button
              type="button"
              className={btnPrimary}
              aria-haspopup="menu"
              aria-expanded={picking}
              onClick={() => setPicking((v) => !v)}
            >
              <ArrowUpIcon />
              Upload recordings
            </button>
            {picking && (
              <div role="menu" className={`${card} absolute right-0 top-full mt-1.5 z-20 w-48 p-1 flex flex-col shadow-[0_8px_24px_-12px_theme(colors.muted)]`}>
                <button type="button" role="menuitem" autoFocus onClick={pickFiles} className="min-h-tap px-3 rounded-[8px] text-left cursor-pointer hover:bg-pill">
                  Choose files…
                </button>
                <button type="button" role="menuitem" onClick={pickFolder} className="min-h-tap px-3 rounded-[8px] text-left cursor-pointer hover:bg-pill">
                  Choose a folder…
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <input ref={fileInput} type="file" accept=".wav,.mp3,.m4a,audio/*" multiple hidden onChange={onPicked} />
      <input
        ref={folderInput}
        type="file"
        multiple
        hidden
        onChange={onPicked}
        // not in React's types; the attribute has to be there for the folder picker
        {...({ webkitdirectory: '' } as object)}
      />
      <UploadZone
        agents={agents}
        agent={agent}
        onAgentChange={setAgent}
        onFiles={send}
        onBrowse={() => setPicking(true)}
        progress={upload.progress}
        notice={notice}
        error={upload.isError ? upload.error.message : null}
      />
      <StatsRow />
      <CallsTable filter={filter} onFilter={setFilter} search={search} onSearch={setSearch} q={q} page={page} onPage={setPage} />
    </div>
  )
}
