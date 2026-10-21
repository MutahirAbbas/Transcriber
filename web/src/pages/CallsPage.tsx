import { useEffect, useRef, useState } from 'react'

import { csvUrl } from '../api/client'
import { useAgents, useUpload } from '../api/hooks'
import type { Filter } from '../api/types'
import { CallsTable } from '../components/calls/CallsTable'
import { ProcessingStrip } from '../components/calls/ProcessingStrip'
import { StatsRow } from '../components/calls/StatsRow'
import { UploadZone } from '../components/calls/UploadZone'
import { btnPrimary, btnSecondary } from '../lib/ui'
import { useDebounced } from '../lib/useDebounced'

export function CallsPage() {
  const fileInput = useRef<HTMLInputElement>(null)
  const { data: agents = [] } = useAgents()
  const [agent, setAgent] = useState('')
  const upload = useUpload()

  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search, 300)
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [filter, q])

  const send = (files: File[]) => {
    if (files.length) upload.mutate({ files, agent })
  }
  const browse = () => fileInput.current?.click()

  return (
    <div className="flex flex-col gap-7">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-h1 font-semibold tracking-tightest">Calls</h1>
          <p className="m-0 text-muted">
            Upload recordings. Each one is transcribed, scored against the scorecard, and ready for review in a minute or two.
          </p>
        </div>
        <div className="flex gap-2.5 flex-wrap">
          <a href={csvUrl(filter, q)} download className={btnSecondary}>
            Export CSV
          </a>
          <button type="button" className={btnPrimary} onClick={browse}>
            Upload recordings
          </button>
        </div>
      </header>

      <input
        ref={fileInput}
        type="file"
        accept="audio/*"
        multiple
        hidden
        onChange={(e) => {
          send(Array.from(e.target.files ?? []))
          e.target.value = '' // so choosing the same file again still fires
        }}
      />
      <UploadZone
        agents={agents}
        agent={agent}
        onAgentChange={setAgent}
        onFiles={send}
        onBrowse={browse}
        uploading={upload.isPending ? (upload.variables?.files.length ?? 0) : 0}
        error={upload.isError ? upload.error.message : null}
      />
      <ProcessingStrip />
      <StatsRow />
      <CallsTable
        filter={filter}
        onFilter={setFilter}
        search={search}
        onSearch={setSearch}
        q={q}
        page={page}
        onPage={setPage}
      />
    </div>
  )
}
