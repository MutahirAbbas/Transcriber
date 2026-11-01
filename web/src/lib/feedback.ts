// "Copy feedback": what a team lead pastes to an agent. It must carry no customer name, age or
// state and no sale amounts, and the summary and work-on-next are free text that can mention any
// of those, so they're scrubbed here rather than trusted.
import type { CallDetail } from '../api/types'

const NUMBER_WORDS_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
const NUMBER_WORDS_UNITS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const word = (s: string) => new RegExp(`\\b${escapeRe(s)}\\b`, 'gi')

function ageInWords(age: number): string | null {
  if (age < 20 || age > 99) return null
  const units = NUMBER_WORDS_UNITS[age % 10]
  const tens = NUMBER_WORDS_TENS[Math.floor(age / 10)]
  return units ? `${tens}[- ]${units}` : (tens ?? null)
}

const AMOUNT = '[amount]'
const COUNTING = 'a|and|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand'
const SPOKEN_AMOUNT = new RegExp(`\\b(?:(?:${COUNTING})[- ]+)+dollars\\b`, 'gi')

export function scrub(text: string, call: Pick<CallDetail, 'customer' | 'sale'>): string {
  let out = text
  const { first_name: first, last_name: last, age, state } = call.customer ?? {}

  if (first && last) out = out.replace(word(`${first} ${last}`), 'the customer')
  if (last) out = out.replace(new RegExp(`\\b(?:Mr|Mrs|Ms|Miss|Dr)\\.?\\s+${escapeRe(last)}\\b`, 'gi'), 'the customer')
  for (const name of [last, first]) if (name) out = out.replace(word(name), 'the customer')
  if (state) out = out.replace(word(state), 'their state')

  // ages, spoken or written: "75-year-old", "aged 75", then the bare number if we know it
  out = out.replace(/\b\d{1,3}[- ]years?[- ]old\s*/gi, '').replace(/\b(?:aged?|at age)\s+\d{1,3}\b/gi, '')
  if (age != null) {
    const spoken = ageInWords(age)
    if (spoken) {
      out = out.replace(new RegExp(`\\b${spoken}[- ]years?[- ]old\\s*`, 'gi'), '').replace(new RegExp(`\\b${spoken}\\b`, 'gi'), '[age]')
    }
    out = out.replace(new RegExp(`\\b${age}\\b`, 'g'), '[age]')
  }

  // money, in any form a summary might use
  out = out
    .replace(/\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b)?(?:\s?(?:\/|per |a )\s?(?:mo|month|yr|year)\b)?/g, AMOUNT)
    .replace(/\b\d[\d,]*(?:\.\d+)?\s*(?:dollars?|bucks)\b/gi, AMOUNT)
    .replace(SPOKEN_AMOUNT, AMOUNT)
  for (const n of [call.sale?.coverage_usd, call.sale?.premium_monthly_usd]) {
    if (n != null) {
      const plain = String(Math.round(n))
      const grouped = Math.round(n).toLocaleString('en-US')
      out = out.replace(new RegExp(`\\b(?:${escapeRe(grouped)}|${plain})\\b`, 'g'), AMOUNT)
    }
  }
  return out.replace(/[ \t]{2,}/g, ' ').replace(/\s+([,.;])/g, '$1').replace(/,(\s*,)+/g, ',').trim()
}

const dateOnly = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })

export function agentNameOf(call: Pick<CallDetail, 'agent' | 'suggested_agent'>): string | null {
  return call.agent?.name ?? call.suggested_agent ?? null
}

/** Agent, date, score, summary, work on next. The call id isn't included: it holds a phone number. */
export function feedbackText(call: CallDetail): string {
  const agent = agentNameOf(call)
  const work = call.score?.work_on_next
  const lines = [
    agent ? `Feedback for ${agent}` : 'Call feedback',
    `${dateOnly(call.uploaded_at)} · Score ${call.overall_score ?? '—'} / 100`,
    '',
    scrub(call.score?.summary ?? '', call),
  ]
  if (work?.items.length) {
    lines.push('', 'Work on next', scrub(work.intro, call))
    work.items.forEach((item, i) => lines.push(`${i + 1}. ${scrub(item, call)}`))
  }
  return lines.join('\n')
}
