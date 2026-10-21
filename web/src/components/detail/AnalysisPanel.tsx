import type { ReactNode } from 'react'

import type { CallDetail } from '../../api/types'
import { categoryLabel, formatTimestamp, parseTimestamp, toneBg, toneOf10 } from '../../lib/format'
import { checkPhrases } from '../../lib/moments'
import { card } from '../../lib/ui'
import { Bar } from '../Badges'
import { CheckIcon, CrossIcon } from '../Icons'

function Panel({ title, children, gap = 'gap-3' }: { title: string; children: ReactNode; gap?: string }) {
  return (
    <section aria-label={title} className={`${card} px-5 py-[18px] flex flex-col ${gap}`}>
      <h2 className="m-0 text-h2 font-semibold">{title}</h2>
      {children}
    </section>
  )
}

const Mono = ({ children }: { children: ReactNode }) => <span className="font-mono text-sm text-muted">{children}</span>

function rtfLabel(rtf: number): string {
  return `${rtf < 1 ? rtf.toFixed(2) : rtf.toFixed(1)}× real time`
}

export function AnalysisPanel({ call, className }: { call: CallDetail; className: string }) {
  const score = call.score
  if (!score) return null
  const segments = call.transcript?.segments ?? []
  const order = call.scorecard.categories
  const cats = [...order.filter((c) => c in score.scores), ...Object.keys(score.scores).filter((c) => !order.includes(c))]
  const phrases = checkPhrases(call.scorecard.required_phrases, segments, score)
  const footer = [
    score._meta?.scorer_model && `Scored by ${score._meta.scorer_model}`,
    call.transcript && `transcribed with ${call.transcript.provider}/${call.transcript.model}`,
    call.rtf != null && rtfLabel(call.rtf),
  ].filter(Boolean)

  return (
    <aside aria-label="AI analysis" className={`${className} flex flex-col gap-4`}>
      <Panel title="Scorecard" gap="gap-3.5">
        {cats.map((cat) => {
          const { score: value, evidence } = score.scores[cat]!
          return (
            <div key={cat} className="flex flex-col gap-1.5">
              <div className="flex justify-between items-baseline">
                <span className="font-medium">{categoryLabel(cat)}</span>
                <span className="font-mono text-md">
                  {value}
                  <span className="text-muted">/10</span>
                </span>
              </div>
              <Bar value={value / 10} className={toneBg[toneOf10(value)]} />
              {evidence && <span className="text-sm text-muted">{evidence}</span>}
            </div>
          )
        })}
      </Panel>

      <Panel title="Compliance">
        <ul className="m-0 p-0 list-none flex flex-col gap-2">
          {phrases.map(({ phrase, present, at }) => (
            <li key={phrase} className="flex items-start gap-2.5">
              {present ? <CheckIcon className="text-green shrink-0 mt-[3px]" /> : <CrossIcon className="text-red shrink-0 mt-[3px]" />}
              <span>
                “{phrase.charAt(0).toUpperCase() + phrase.slice(1)}”{' '}
                {present ? (
                  <>
                    stated {at != null && <Mono>{formatTimestamp(at)}</Mono>}
                  </>
                ) : (
                  <span className="text-red">missing</span>
                )}
              </span>
            </li>
          ))}
          {score.compliance.red_flags.map((f, i) => {
            const t = parseTimestamp(f.timestamp)
            return (
              <li key={`flag-${i}`} className="flex items-start gap-2.5 text-red">
                <CrossIcon className="shrink-0 mt-[3px]" />
                <span>
                  {f.flag} {t != null && <Mono>{formatTimestamp(t)}</Mono>}
                </span>
              </li>
            )
          })}
        </ul>
      </Panel>

      {score.strengths.length > 0 && (
        <Panel title="Strengths">
          <ul className="m-0 pl-[18px] flex flex-col gap-1.5 text-text-2 list-disc">
            {score.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Panel>
      )}

      {score.improvements.length > 0 && (
        <Panel title="Work on next">
          <ul className="m-0 pl-[18px] flex flex-col gap-1.5 text-text-2 list-disc">
            {score.improvements.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </Panel>
      )}

      {footer.length > 0 && <div className="text-sm text-muted px-1">{footer.join(' · ')}</div>}
    </aside>
  )
}
