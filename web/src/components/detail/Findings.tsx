import { useState } from 'react'

import type { Finding } from '../../api/types'
import { categoryLabel } from '../../lib/format'
import { segOff, segOn, segTrack } from '../../lib/ui'

type Kind = 'all' | Finding['kind']

const TAG: Record<Finding['kind'], { word: string; tag: string; category: string }> = {
  flag: { word: 'FLAG', tag: 'bg-rust-tint text-rust-ink', category: 'text-rust-ink' },
  coach: { word: 'COACH', tag: 'bg-link-tint text-link-ink', category: 'text-link-ink' },
  good: { word: 'GOOD', tag: 'bg-pill text-muted', category: 'text-muted' },
}

const PILL_TEXT: Record<Kind, string> = { all: 'text-ink', flag: '!text-rust-ink', coach: '!text-link-ink', good: '' }

export function Findings({ findings, labels, onSelect, className }: {
  findings: Finding[] | null
  labels: Record<string, string>
  onSelect: (f: Finding) => void
  className: string
}) {
  const [kind, setKind] = useState<Kind>('all')
  const all = findings ?? []
  const count = (k: Finding['kind']) => all.filter((f) => f.kind === k).length
  const shown = kind === 'all' ? all : all.filter((f) => f.kind === kind)
  const pills: [Kind, string][] = [
    ['all', 'All'],
    ['flag', `Flags ${count('flag')}`],
    ['coach', `Coach ${count('coach')}`],
    ['good', `Good ${count('good')}`],
  ]
  return (
    <section aria-label="Findings" className={`flex flex-col min-h-0 ${className}`}>
      <header className="flex flex-wrap items-center justify-between gap-x-1 gap-y-2 pl-5 pr-3 py-3.5 border-b border-divider">
        <h2 className="m-0 text-lg font-medium">
          Findings <span className="text-muted font-normal">{all.length}</span>
        </h2>
        <div className={segTrack} role="group" aria-label="Filter findings">
          {pills.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={kind === value}
              onClick={() => setKind(value)}
              className={`${kind === value ? segOn : segOff} !px-2 ${PILL_TEXT[value]}`}
            >
              {label}
            </button>
          ))}
        </div>
      </header>
      <ul className="m-0 p-0 list-none flex-1 min-h-0 overflow-y-auto divide-y divide-divider">
        {findings == null && <li className="px-5 py-8 text-muted text-md">Scored before scorecard v2. Re-score to see findings.</li>}
        {findings != null && !shown.length && <li className="px-5 py-8 text-muted text-md">Nothing here.</li>}
        {shown.map((f, i) => {
          const t = TAG[f.kind]
          return (
            <li key={`${f.timestamp}-${i}`}>
              <button
                type="button"
                onClick={() => onSelect(f)}
                className="w-full text-left px-5 py-4 flex gap-3 cursor-pointer hover:bg-faint"
              >
                <span className="flex flex-col items-start gap-1.5 shrink-0 w-[62px]">
                  <span className="font-mono text-md">{f.timestamp}</span>
                  <span className={`font-mono text-xs font-medium px-1.5 py-0.5 rounded-tag ${t.tag}`}>{t.word}</span>
                </span>
                <span className="flex flex-col gap-1 min-w-0">
                  <span className={`text-md font-medium ${t.category}`}>{labels[f.category] ?? categoryLabel(f.category)}</span>
                  <span>{f.text}</span>
                  {f.better && (
                    <span className="text-md text-muted">
                      <strong className="font-medium text-ink">Better:</strong> {f.better}
                    </span>
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
