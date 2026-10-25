import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { api } from './client'
import type { CallStatus, CallSummary, ListParams } from './types'

const POLL_MS = 2000

export const isActive = (c: { status: CallStatus }) => c.status !== 'done' && c.status !== 'error'

const keys = {
  calls: ['calls'] as const,
  inProgress: ['in-progress'] as const,
  stats: ['stats'] as const,
  agents: ['agents'] as const,
  call: (id: string) => ['call', id] as const,
}

/** Everything that changes when a call finishes, is uploaded, re-scored or deleted. */
function useInvalidateCalls() {
  const qc = useQueryClient()
  return () => {
    for (const key of [keys.calls, keys.inProgress, keys.stats, keys.agents]) {
      void qc.invalidateQueries({ queryKey: key })
    }
  }
}

export function useCalls(params: ListParams) {
  return useQuery({
    queryKey: [...keys.calls, params],
    queryFn: () => api.listCalls(params),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => (q.state.data?.items.some(isActive) ? POLL_MS : false),
  })
}

export function useInProgress() {
  const invalidate = useInvalidateCalls()
  const query = useQuery({
    queryKey: keys.inProgress,
    queryFn: api.inProgress,
    refetchInterval: (q) => (q.state.data?.some(isActive) ? POLL_MS : false),
  })
  // when a call leaves the active set it landed in the table: refresh table + stats
  const active = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!query.data) return
    const now = new Set(query.data.filter(isActive).map((c) => c.id))
    const finished = [...active.current].some((callId) => !now.has(callId))
    active.current = now
    if (finished) invalidate()
  }, [query.data])
  return query
}

/** Remote mode only: is the Mac transcriber connected? */
export const useWorkerStatus = (enabled: boolean) =>
  useQuery({ queryKey: ['worker-status'], queryFn: api.workerStatus, enabled, refetchInterval: 30_000 })

export const useMe = () => useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: Infinity })

export const useStats = () => useQuery({ queryKey: keys.stats, queryFn: api.stats })
export const useAgents = () => useQuery({ queryKey: keys.agents, queryFn: api.agents })
export const useSettings = () =>
  useQuery({ queryKey: ['settings'], queryFn: api.settings, staleTime: Infinity })

export function useCall(callId: string) {
  return useQuery({
    queryKey: keys.call(callId),
    queryFn: () => api.getCall(callId),
    refetchInterval: (q) => (q.state.data && isActive(q.state.data) ? POLL_MS : false),
  })
}

export function useUpload() {
  const invalidate = useInvalidateCalls()
  return useMutation({
    // One request per file: each stays under the proxy's per-request size limit, each shows up
    // in the strip as soon as it lands, and one bad file doesn't stop the rest.
    mutationFn: async ({ files, agent }: { files: File[]; agent: string }) => {
      const created: CallSummary[] = []
      const failed: string[] = []
      for (const file of files) {
        try {
          created.push(...(await api.upload([file], agent)))
          invalidate()
        } catch (err) {
          const msg = err instanceof Error ? err.message : 'upload failed'
          failed.push(msg.includes(file.name) ? msg : `${file.name}: ${msg}`)
        }
      }
      if (failed.length) throw new Error(failed.join(' · '))
      return created
    },
    onSettled: invalidate,
  })
}

export function useRescore(callId: string) {
  const qc = useQueryClient()
  const invalidate = useInvalidateCalls()
  return useMutation({
    mutationFn: () => api.rescore(callId),
    onSuccess: () => {
      invalidate()
      void qc.invalidateQueries({ queryKey: keys.call(callId) })
    },
  })
}

export function useDeleteCall() {
  const qc = useQueryClient()
  const invalidate = useInvalidateCalls()
  return useMutation({
    mutationFn: (callId: string) => api.deleteCall(callId),
    onSuccess: (_data, callId) => {
      qc.removeQueries({ queryKey: keys.call(callId) })
      invalidate()
    },
  })
}
