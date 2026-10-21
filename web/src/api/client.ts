import type {
  Agent, CallDetail, CallList, CallSummary, Filter, ListParams, Stats, TranscriberSettings,
} from './types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) {
    let message = res.statusText || `Request failed (${res.status})`
    try {
      const body: unknown = await res.json()
      if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
        message = body.detail
      }
    } catch {
      // not JSON — keep the status text
    }
    throw new ApiError(res.status, message)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

const id = (callId: string) => encodeURIComponent(callId)

function filterQuery(filter: Filter, q: string): URLSearchParams {
  const params = new URLSearchParams({ filter })
  if (q.trim()) params.set('q', q.trim())
  return params
}

export const api = {
  listCalls({ filter, q, page, pageSize }: ListParams) {
    const params = filterQuery(filter, q)
    params.set('page', String(page))
    params.set('page_size', String(pageSize))
    return request<CallList>(`/calls?${params}`)
  },
  inProgress: () => request<CallSummary[]>('/calls/in-progress'),
  getCall: (callId: string) => request<CallDetail>(`/calls/${id(callId)}`),
  upload(files: File[], agent: string) {
    const body = new FormData()
    for (const f of files) body.append('files', f)
    body.append('agent', agent)
    return request<CallSummary[]>('/calls', { method: 'POST', body })
  },
  rescore: (callId: string) => request<CallSummary>(`/calls/${id(callId)}/rescore`, { method: 'POST' }),
  deleteCall: (callId: string) => request<void>(`/calls/${id(callId)}`, { method: 'DELETE' }),
  stats: () => request<Stats>('/stats'),
  agents: () => request<Agent[]>('/agents'),
  settings: () => request<TranscriberSettings>('/settings'),
}

export const csvUrl = (filter: Filter, q: string) => `/api/calls.csv?${filterQuery(filter, q)}`
export const audioUrl = (callId: string) => `/api/calls/${id(callId)}/audio`
export const transcriptUrl = (callId: string) => `/api/calls/${id(callId)}/transcript.txt`
