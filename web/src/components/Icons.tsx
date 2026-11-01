import type { SVGProps } from 'react'

// All decorative: aria-hidden.
type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Icon({ size = 18, strokeWidth = 1.8, children, ...rest }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const LogoIcon = (p: IconProps) => (
  <Icon size={22} strokeWidth={2} {...p}>
    <path d="M3 12h2M8 8v8M12 4v16M16 8v8M20 11v2" />
  </Icon>
)

export const CallsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 6h16M4 12h16M4 18h10" />
  </Icon>
)

export const AgentsIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="8" r="4" />
    <path d="M5 21c0-4 3.1-6 7-6s7 2 7 6" />
  </Icon>
)

export const ScorecardIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 12l2 2 4-4" />
    <rect x="4" y="4" width="16" height="16" rx="3" />
  </Icon>
)

export const SettingsIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" />
  </Icon>
)

export const UploadIcon = (p: IconProps) => (
  <Icon size={22} {...p}>
    <path d="M12 16V5M7 10l5-5 5 5" />
    <path d="M4 20h16" />
  </Icon>
)

export const ArrowUpIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2} {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Icon>
)

export const SearchIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2} {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="M20 20l-3.5-3.5" />
  </Icon>
)

export const WarnIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2} {...p}>
    <path d="M12 9v4M12 17h.01" />
    <path d="M10.3 3.9L2.5 18a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  </Icon>
)

export const BackIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2} {...p}>
    <path d="M15 5l-7 7 7 7" />
  </Icon>
)

export const PlayIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={0} fill="currentColor" {...p}>
    <path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z" />
  </Icon>
)

export const PauseIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={0} fill="currentColor" {...p}>
    <rect x="6" y="4" width="4.5" height="16" rx="1" />
    <rect x="13.5" y="4" width="4.5" height="16" rx="1" />
  </Icon>
)
