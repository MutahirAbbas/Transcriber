export type CallStatus = 'queued' | 'transcribing' | 'scoring' | 'done' | 'error'
export type Outcome = 'sale' | 'callback' | 'no_sale' | 'not_interested' | 'wrong_number' | 'unclear'
export type Filter = 'all' | 'sales' | 'flagged' | 'below60'

export interface CallSummary {
  id: string
  filename: string
  agent_id: number | null
  agent: string | null
  uploaded_at: string
  duration_s: number | null
  stereo: boolean | null
  status: CallStatus
  progress: number
  error: string | null
  provider: string | null
  model: string | null
  scored_at: string | null
  overall_score: number | null
  outcome: Outcome | null
  flag_count: number
  weakest_category: string | null
}

export interface CallList {
  items: CallSummary[]
  total: number
}

export interface Segment {
  start: number
  end: number
  text: string
  speaker: string | null
  confidence: number | null
}

export interface Transcript {
  provider: string
  model: string
  call_id: string
  segments: Segment[]
  latency_s: number
  audio_duration_s: number
  cost_usd: number
}

export interface CoachableMoment {
  timestamp: string
  what_happened: string
  better_approach: string
}

export interface Score {
  agent_speaker: string | null
  agent_name?: string | null
  call_outcome: Outcome
  scores: Record<string, { score: number; evidence: string }>
  overall_score: number
  strengths: string[]
  improvements: string[]
  coachable_moments: CoachableMoment[]
  compliance: {
    required_phrases_present: boolean
    missing: string[]
    red_flags: { timestamp: string; flag: string }[]
  }
  summary: string
  _meta?: { scorer_model?: string; transcriber?: string }
}

export interface CallDetail extends CallSummary {
  transcript: Transcript | null
  score: Score | null
  speaker_map: Record<string, string>
  rtf: number | null
  scorecard: { categories: string[]; required_phrases: string[] }
}

export interface Stats {
  calls_scored: number
  avg_score: number | null
  compliance_flags: number
  weakest_category: string | null
}

export interface Agent {
  id: number
  name: string
  slug: string
}

export type WorkerMode = 'local' | 'remote'

export interface TranscriberSettings {
  provider: string
  model: string
  worker_mode: WorkerMode
}

export interface ListParams {
  filter: Filter
  q: string
  page: number
  pageSize: number
}
