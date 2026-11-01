import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { isInProgress } from '../lib/stage'
import { api } from './client'
import type { CallSummary, ListParams, Stats } from './types'

const POLL_BUSY_MS = 2000
const POLL_IDLE_MS = 30_000

const keys = {
  calls: ['calls'] as const,
  stats: ['stats'] as const,
  agents: ['agents'] as const,
  call: (id: string) => ['call', id] as const,
}

/** Everything that changes when a call finishes, is uploaded, re-scored, assigned or deleted. */
function useInvalidateCalls() {
  const qc = useQueryClient()
  return () => {
    for (const key of [keys.calls, keys.stats, keys.agents]) {
      void qc.invalidateQueries({ queryKey: key })
    }
  }
}

export function useCalls(params: ListParams) {
  const qc = useQueryClient()
  return useQuery({
    queryKey: [...keys.calls, params],
    queryFn: () => api.listCalls(params),
    placeholderData: keepPreviousData,
    // the stats count calls on every page, so a job on page 2 still keeps this one fresh
    refetchInterval: (q) =>
      q.state.data?.items.some(isInProgress) || qc.getQueryData<Stats>(keys.stats)?.in_progress ? POLL_BUSY_MS : POLL_IDLE_MS,
  })
}

/** Remote mode only: is the Mac transcriber connected? */
export const useWorkerStatus = (enabled: boolean) =>
  useQuery({ queryKey: ['worker-status'], queryFn: api.workerStatus, enabled, refetchInterval: 30_000 })

export const useMe = () => useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: Infinity })

export const useStats = () =>
  useQuery({
    queryKey: keys.stats,
    queryFn: api.stats,
    refetchInterval: (q) => (q.state.data?.in_progress ? POLL_BUSY_MS : POLL_IDLE_MS),
  })
export const useAgents = () => useQuery({ queryKey: keys.agents, queryFn: api.agents })
export const useSettings = () =>
  useQuery({ queryKey: ['settings'], queryFn: api.settings, staleTime: Infinity })

export function useCall(callId: string) {
  return useQuery({
    queryKey: keys.call(callId),
    queryFn: () => api.getCall(callId),
    refetchInterval: (q) => (q.state.data && isInProgress(q.state.data) ? POLL_BUSY_MS : false),
  })
}

export function useUpload() {
  const invalidate = useInvalidateCalls()
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const mutation = useMutation({
    // One request per file, in turn: each stays under the proxy's per-request size limit, each shows
    // up in the table as soon as it lands, and one bad file doesn't stop the rest.
    mutationFn: async ({ files, agent }: { files: File[]; agent: string }) => {
      const created: CallSummary[] = []
      const failed: string[] = []
      setProgress({ done: 0, total: files.length })
      for (const [i, file] of files.entries()) {
        try {
          created.push(...(await api.upload([file], agent)))
          invalidate()
        } catch (err) {
          const msg = err instanceof Error ? err.message : 'upload failed'
          failed.push(msg.includes(file.name) ? msg : `${file.name}: ${msg}`)
        }
        setProgress({ done: i + 1, total: files.length })
      }
      if (failed.length) throw new Error(failed.join(' · '))
      return created
    },
    onSettled: () => {
      setProgress(null)
      invalidate()
    },
  })
  return { ...mutation, progress }
}

export function useCancel() {
  const qc = useQueryClient()
  const invalidate = useInvalidateCalls()
  return useMutation({
    mutationFn: (callId: string) => api.cancel(callId),
    onSuccess: (_data, callId) => {
      qc.removeQueries({ queryKey: keys.call(callId) })
      invalidate()
    },
  })
}

/** Assign an existing agent, or a name heard on the call that may not be an agent yet. */
export function useAssign() {
  const qc = useQueryClient()
  const invalidate = useInvalidateCalls()
  return useMutation({
    mutationFn: async ({ callId, agentId, name }: { callId: string; agentId?: number; name?: string }) => {
      const id = agentId ?? (await api.createAgent(name ?? '')).id
      return api.assign(callId, id)
    },
    onSuccess: (_data, { callId }) => {
      invalidate()
      void qc.invalidateQueries({ queryKey: keys.call(callId) })
    },
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
