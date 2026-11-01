// Shared class strings for the design's controls.
export const btn =
  'inline-flex items-center justify-center gap-2 min-h-tap px-5 rounded-control border font-medium cursor-pointer disabled:cursor-not-allowed disabled:opacity-60'
export const btnSecondary = `${btn} border-border bg-surface text-ink hover:bg-faint hover:text-ink`
export const btnPrimary = `${btn} border-ink bg-ink text-surface hover:opacity-90 hover:text-surface`

// The grey track with one white segment: filter pills on Calls, findings pills on detail
export const segTrack = 'inline-flex flex-wrap gap-0.5 p-1 rounded-control bg-pill'
export const segItem = 'min-h-tap md:min-h-9 px-3.5 rounded-[8px] text-md cursor-pointer'
export const segOn = `${segItem} bg-surface text-ink shadow-[0_0_0_1px_theme(colors.divider)]`
export const segOff = `${segItem} text-muted hover:text-ink`

export const card = 'bg-surface border border-border rounded-card'
export const input = 'min-h-tap px-3 rounded-control border border-border bg-surface text-ink focus:border-link'
