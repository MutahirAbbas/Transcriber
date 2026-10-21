import type { Config } from 'tailwindcss'

// Tokens from BUILD_BRIEF.md / design/*.html. `colors` replaces Tailwind's palette
// entirely, so components can only use these names — no raw hexes.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      ground: '#F3F4F6',
      surface: '#FFFFFF',
      border: { DEFAULT: '#E3E5EA', subtle: '#EEF0F4' },
      control: '#D5D8DF', // button / input borders
      dropzone: '#B9BEC9', // dashed upload border
      ink: '#15171A',
      'text-2': '#374151',
      muted: '#6B7280',
      nav: '#4B5563',
      accent: { DEFAULT: '#2F4BC9', hover: '#1F37A3' },
      green: '#1B7F5A',
      amber: '#B45309',
      red: '#B42318',
      sale: { bg: '#E3F3EC', text: '#166A48' },
      badge: { bg: '#EEF0F4', text: '#374151' },
      customer: '#9CA3AF', // transcript legend swatch
      moment: {
        bg: '#FDF3E7',
        border: '#F0C9A0',
        swatch: '#FDE7CF',
        title: '#92400E',
        body: '#78350F',
      },
    },
    fontFamily: {
      sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
      mono: ['"IBM Plex Mono"', 'monospace'],
    },
    fontSize: {
      '2xs': '10px',
      xs: '11px',
      sm: '12px',
      md: '13px',
      base: ['14px', '1.5'],
      h2: '15px',
      lg: '18px',
      h1d: '20px', // page h1 on detail
      h1: '26px',
    },
    borderRadius: {
      none: '0',
      bar: '3px',
      swatch: '3px',
      logo: '7px',
      control: '8px', // buttons / inputs
      'card-sm': '10px',
      card: '12px',
      full: '999px',
    },
    extend: {
      letterSpacing: {
        tightest: '-0.02em',
        snug: '-0.015em',
        tight: '-0.01em',
        head: '0.05em',
        label: '0.06em',
      },
      minHeight: { tap: '44px' },
      minWidth: { tap: '44px' },
    },
  },
  plugins: [],
} satisfies Config
