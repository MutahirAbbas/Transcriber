// Shared class strings for the design's controls.
export const btn =
  'inline-flex items-center justify-center min-h-tap px-4 rounded-control border font-medium cursor-pointer disabled:cursor-not-allowed disabled:opacity-60'
export const btnSecondary = `${btn} border-control bg-surface text-ink hover:bg-ground hover:text-ink`
export const btnPrimary = `${btn} border-ink bg-ink text-surface hover:opacity-90 hover:text-surface`

// 36px as designed on desktop; 44px touch targets on phones
export const pill = 'min-h-tap md:min-h-9 px-3 rounded-full border text-md cursor-pointer'
export const pillOn = `${pill} border-ink bg-ink text-surface`
export const pillOff = `${pill} border-control bg-surface text-ink hover:bg-ground`

export const card = 'bg-surface border border-border rounded-card'
export const smallCard = 'bg-surface border border-border rounded-card-sm'
