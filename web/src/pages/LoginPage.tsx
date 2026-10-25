import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { api, safeNext } from '../api/client'
import { useMe } from '../api/hooks'
import { LogoIcon } from '../components/Icons'
import { btnPrimary, card } from '../lib/ui'
import { useTitle } from '../lib/useTitle'

const input = 'min-h-tap px-3 rounded-control border border-control bg-surface text-ink focus:border-accent'

export function LoginPage() {
  useTitle('Sign in')
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const { data: me } = useMe()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  // already signed in (or sign-in is off in local dev): nothing to do here
  useEffect(() => {
    if (me?.email || me?.auth_disabled) navigate(next, { replace: true })
  }, [me, next, navigate])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setPending(true)
    setError(null)
    try {
      await api.login(email, password)
      queryClient.clear()
      navigate(next, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed')
      setPending(false)
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-ground px-4 py-10">
      <form onSubmit={submit} className={`${card} w-full max-w-[380px] px-6 py-7 flex flex-col gap-5`} noValidate>
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-logo bg-ink text-surface flex items-center justify-center">
            <LogoIcon />
          </div>
          <span className="font-semibold text-h2 tracking-tight">Call Analyzer</span>
        </div>
        <h1 className="m-0 text-h1d font-semibold tracking-snug">Sign in</h1>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="email" className="text-sm text-muted font-medium">
            Email or username
          </label>
          <input id="email" type="text" autoComplete="username" autoCapitalize="none" spellCheck={false}
                 required autoFocus value={email}
                 onChange={(e) => setEmail(e.target.value)} className={input} />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="password" className="text-sm text-muted font-medium">
            Password
          </label>
          <input id="password" type="password" autoComplete="current-password" required value={password}
                 onChange={(e) => setPassword(e.target.value)} className={input} />
        </div>
        {error && (
          <p role="alert" className="m-0 text-red text-md">
            {error}
          </p>
        )}
        <button type="submit" className={`${btnPrimary} w-full`} disabled={pending || !email || !password}>
          {pending ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
