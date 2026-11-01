import type { Config } from 'tailwindcss'

// Tokens from design/v2 (sampled from the screenshots). `colors` replaces Tailwind's palette
// entirely, so components can only use these names — no raw hexes.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      ground: '#F2F2F0',
      surface: '#FFFFFF', // cards
      faint: '#FAFAF8', // upload zone, in-progress rows
      border: '#E6E6E2',
      divider: '#ECECE8',
      ink: '#141518',
      'text-2': '#1F2126',
      muted: '#5E6168',
      wave: '#D9D9D4',
      link: { DEFAULT: '#1F4FE0', ink: '#1537A8', tint: '#E9EFFF', wash: '#F6F8FF' },
      rust: { DEFAULT: '#B4530A', ink: '#8A4A12', tint: '#FFF1E3', wash: '#FFF6EC', edge: '#F0D9C0' },
      pill: '#F2F2F0',
      band: { DEFAULT: '#141518', text: '#E6E6E2', muted: '#A9ACB3', edge: '#3A3C42' },
      phase: { neutral: '#E6E6E2', light: '#F6E3CE', deep: '#F0C9A3' },
    },
    fontFamily: {
      sans: ['Inter', 'system-ui', 'sans-serif'],
      mono: ['ui-monospace', '"SF Mono"', 'Menlo', 'Consolas', 'monospace'],
    },
    fontSize: {
      xs: '12px',
      sm: '13px',
      md: '14px',
      base: ['15px', '1.45'],
      lg: '17px',
      xl: '18px',
      h3: '22px',
      h2: '30px',
      stat: '34px',
      score: ['104px', '1'],
    },
    borderRadius: {
      none: '0',
      bar: '3px',
      tag: '5px',
      phase: '6px',
      control: '10px',
      tile: '12px',
      card: '16px',
      full: '999px',
    },
    extend: {
      letterSpacing: {
        tightest: '-0.03em',
        snug: '-0.02em',
        tight: '-0.01em',
        head: '0.05em',
      },
      minHeight: { tap: '44px' },
      minWidth: { tap: '44px' },
    },
  },
  plugins: [],
} satisfies Config
