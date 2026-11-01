// "Copy feedback": what a team lead pastes to an agent. It must carry no customer name, age or
// state and no sale amounts, and the summary and work-on-next are free text that can mention any
// of those, so they're scrubbed here rather than trusted.
import type { CallDetail } from '../api/types'

const TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
const UNITS = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']
const SPOKEN_TENS = TENS.filter(Boolean).join('|')
const SPOKEN_UNITS = UNITS.filter(Boolean).join('|')

const STATES: Record<string, string> = {
  Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA', Colorado: 'CO', Connecticut: 'CT',
  Delaware: 'DE', Florida: 'FL', Georgia: 'GA', Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL', Indiana: 'IN', Iowa: 'IA',
  Kansas: 'KS', Kentucky: 'KY', Louisiana: 'LA', Maine: 'ME', Maryland: 'MD', Massachusetts: 'MA', Michigan: 'MI',
  Minnesota: 'MN', Mississippi: 'MS', Missouri: 'MO', Montana: 'MT', Nebraska: 'NE', Nevada: 'NV', 'New Hampshire': 'NH',
  'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', Ohio: 'OH',
  Oklahoma: 'OK', Oregon: 'OR', Pennsylvania: 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
  Tennessee: 'TN', Texas: 'TX', Utah: 'UT', Vermont: 'VT', Virginia: 'VA', Washington: 'WA', 'West Virginia': 'WV',
  Wisconsin: 'WI', Wyoming: 'WY', 'District of Columbia': 'DC',
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
// \b only knows ASCII, so it can't see the edge of a name like Émile or Zoë
const NOT_BEFORE = '(?<![\\p{L}\\p{N}_])'
const NOT_AFTER = '(?![\\p{L}\\p{N}_])'
const word = (s: string, flags = 'giu') => new RegExp(`${NOT_BEFORE}${escapeRe(s.normalize('NFC'))}${NOT_AFTER}`, flags)

function ageInWords(age: number): string | null {
  if (age < 20 || age > 99) return null
  const units = UNITS[age % 10]
  const tens = TENS[Math.floor(age / 10)]
  return units ? `${tens}[- ]${units}` : (tens ?? null)
}

const AMOUNT = '[amount]'
const COUNTING = `a|and|${SPOKEN_UNITS}|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|${SPOKEN_TENS}|hundred|thousand`
const SPOKEN_DOLLARS = new RegExp(`\\b(?:(?:${COUNTING})[- ]+)+dollars\\b`, 'gi')
const SPOKEN_LARGE = new RegExp(`\\b(?:(?:${COUNTING})[- ]+)+(?:hundred|thousand)\\b(?:[- ]+(?:${SPOKEN_TENS}|${SPOKEN_UNITS}))*`, 'gi')
const SPOKEN_YEARS_OLD = new RegExp(`\\b(?:${SPOKEN_TENS})(?:[- ](?:${SPOKEN_UNITS}))?[- ]years?[- ]old\\s*`, 'gi')

/** Best effort: this catches the forms a summary uses, and a person should still read what they
 *  paste. It only runs where the customer's name is known; see feedbackText. */
export function scrub(text: string, call: Pick<CallDetail, 'customer' | 'sale'> & Partial<Pick<CallDetail, 'speakers' | 'agent'>>): string {
  let out = text.normalize('NFC').replace(/[‘’]/g, "'")
  const { first_name: first, last_name: last, age, state } = call.customer ?? {}

  if (first && last) out = out.replace(word(`${first} ${last}`), 'the customer')
  if (last) {
    out = out.replace(new RegExp(`${NOT_BEFORE}(?:Mr|Mrs|Ms|Miss|Dr)\\.?\\s+${escapeRe(last.normalize('NFC'))}${NOT_AFTER}`, 'giu'), 'the customer')
  }
  for (const name of [last, first]) if (name) out = out.replace(word(name), 'the customer')
  // whoever else the transcript shows as a speaker, other than the agent
  for (const shown of Object.values(call.speakers ?? {})) {
    if (shown !== call.agent?.name && !['Agent', 'Customer'].includes(shown) && !/^Speaker \d+$/.test(shown)) {
      out = out.replace(word(shown), 'the customer')
    }
  }

  if (state) {
    const trimmed = state.trim()
    const full = Object.keys(STATES).find((n) => n.toLowerCase() === trimmed.toLowerCase() || STATES[n] === trimmed.toUpperCase())
    if (full) {
      out = out.replace(word(full), 'their state').replace(word(STATES[full]!, 'gu'), 'their state')   // MD, but not "or" for OR
    }
    out = out.replace(word(trimmed), 'their state')
  }

  // ages, spoken or written: "75-year-old", "75 yo", "aged 75", "75 years of age", then the number
  out = out
    .replace(/\b\d{1,3}[- ]years?[- ]old\s*/gi, '')
    .replace(SPOKEN_YEARS_OLD, '')
    .replace(/\b\d{2,3}\s?y\.?o\.?(?=\W|$)\s*/gi, '')
    .replace(/\b\d{1,3}\s+years?\s+of\s+age\b/gi, '[age]')
    .replace(/\b(?:aged?|at age)\s+\d{1,3}\b/gi, '')
  if (age != null) {
    const spoken = ageInWords(age)
    if (spoken) out = out.replace(new RegExp(`\\b${spoken}\\b`, 'gi'), '[age]')
    out = out.replace(new RegExp(`\\b${age}\\b`, 'g'), '[age]')
  }

  // money, in any form a summary might use
  out = out
    .replace(/\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM]\b)?(?:\s?(?:\/|per |a )\s?(?:mo|month|yr|year)\b)?/g, AMOUNT)
    .replace(/\b\d[\d,]*(?:\.\d+)?\s*(?:dollars?|bucks)\b/gi, AMOUNT)
    .replace(SPOKEN_DOLLARS, AMOUNT)
    .replace(SPOKEN_LARGE, AMOUNT)
    .replace(/\b\d{1,3}(?:,\d{3})+\b/g, AMOUNT)
    .replace(/\b\d+(?:\.\d+)?\s?[kK]\b/g, AMOUNT)
  for (const n of [call.sale?.coverage_usd, call.sale?.premium_monthly_usd]) {
    if (n != null) out = out.replace(new RegExp(`\\b${Math.round(n)}\\b`, 'g'), AMOUNT)
  }
  return out.replace(/[ \t]{2,}/g, ' ').replace(/\s+([,.;])/g, '$1').replace(/,(\s*,)+/g, ',').trim()
}

const dateOnly = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })

export function agentNameOf(call: Pick<CallDetail, 'agent' | 'suggested_agent'>): string | null {
  return call.agent?.name ?? call.suggested_agent ?? null
}

/** Old-format scores have no customer record, so nothing can be scrubbed: re-score first. */
export const canCopyFeedback = (call: Pick<CallDetail, 'score' | 'scored_v2'>) => !!call.score && call.scored_v2

/** Agent, date, score, summary, work on next. The call id isn't included: it holds a phone number.
 *  With no customer name to look for, a summary can't be made safe, so it's left out (fail closed). */
export function feedbackText(call: CallDetail): string {
  const agent = agentNameOf(call)
  const work = call.score?.work_on_next
  const nameKnown = !!call.customer?.first_name
  const lines = [
    agent ? `Feedback for ${agent}` : 'Call feedback',
    `${dateOnly(call.uploaded_at)} · Score ${call.overall_score ?? '—'} / 100`,
    '',
    nameKnown ? scrub(call.score?.summary ?? '', call) : 'Summary left out: the customer’s name wasn’t identified, so it couldn’t be checked.',
  ]
  if (work?.items.length) {
    lines.push('', 'Work on next')
    if (nameKnown) lines.push(scrub(work.intro, call))
    work.items.forEach((item, i) => lines.push(`${i + 1}. ${scrub(item, call)}`))
  }
  return lines.join('\n')
}
