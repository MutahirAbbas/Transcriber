export type CallStatus = 'queued' | 'transcribing' | 'separating' | 'scoring' | 'done' | 'error'
export type Stage = CallStatus | 'waiting'
export type Outcome = 'sale' | 'callback' | 'no_sale' | 'not_interested' | 'wrong_number' | 'unclear'
export type Filter = 'all' | 'sales' | 'flagged' | 'below_target' | 'unassigned'

export interface CallSummary {
  id: string
  filename: string
  agent_id: number | null
  agent: { id: number; name: string } | null
  suggested_agent: string | null
  uploaded_at: string
  duration_s: number | null
  stereo: boolean | null
  status: CallStatus
  stage: Stage
  progress: number
  eta_s: number | null
  error: string | null
  provider: string | null
  model: string | null
  scored_at: string | null
  overall_score: number | null
  outcome: Outcome | null
  flag_count: number
  critical_count: number
  weakest_category: string | null
  weakest_label: string | null
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

export interface Finding {
  kind: 'flag' | 'coach' | 'good'
  category: string
  timestamp: string
  text: string
  better?: string
  flag_type?: string
  severity?: 'critical' | 'major'
  label?: string
}

export interface PhaseMark {
  phase: string
  start: string
}

export interface Customer {
  first_name: string | null
  last_name: string | null
  age: number | null
  state: string | null
}

export interface Sale {
  coverage_usd: number | null
  premium_monthly_usd: number | null
  carrier: string | null
  plan_type: string
}

export interface Score {
  agent_speaker: string | null
  agent_name?: string | null
  call_outcome: Outcome
  summary: string
  scores: Record<string, { score: number; evidence: string; capped_from?: number }>
  overall_score: number
  work_on_next?: { intro: string; items: string[] }
  _meta?: { scorer_model?: string; requested_model?: string; fallback?: boolean; transcriber?: string }
}

export interface CardPhase {
  id: string
  label: string
  category: string
}

export interface CallDetail extends CallSummary {
  transcript: Transcript | null
  score: Score | null
  scored_v2: boolean
  speakers: Record<string, string>
  speaker_source: string | null
  waveform: number[] | null
  findings: Finding[] | null
  phases: PhaseMark[] | null
  customer: Customer | null
  sale: Sale | null
  scorer_version: string | null
  prev_id: string | null
  next_id: string | null
  agent_average: number | null
  worker_id: string | null
  rtf: number | null
  scorecard: {
    version: number
    categories: string[]
    labels: Record<string, string>
    required_phrases: string[]
    phases: CardPhase[]
    target_score: number
  }
}

export interface Stats {
  calls_scored: number
  avg_score: number | null
  compliance_flags: number
  flagged_calls: number
  weakest_category: string | null
  weakest_label: string | null
  weakest_avg: number | null
  in_progress: number
  target_score: number
}

export interface Agent {
  id: number
  name: string
  slug: string
}

export interface Me {
  email: string | null
  auth_disabled: boolean
}

export interface WorkerStatus {
  online: boolean
  last_seen: string | null
  worker_id: string | null
  model: string | null
}

export type WorkerMode = 'local' | 'remote'

export interface TranscriberSettings {
  provider: string
  model: string
  worker_mode: WorkerMode
  scorer_model: string
}

export interface ListParams {
  filter: Filter
  q: string
  page: number
  pageSize: number
}
