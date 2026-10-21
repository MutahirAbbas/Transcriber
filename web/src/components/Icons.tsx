import type { SVGProps } from 'react'

// Paths from design/*.html. All decorative: aria-hidden.
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
  <Icon size={16} strokeWidth={2.2} {...p}>
    <path d="M4 12v2M8 8v8M12 5v14M16 9v6M20 11v3" />
  </Icon>
)

export const CallsIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z" />
    <path d="M8 9h8M8 13h8M8 17h5" />
  </Icon>
)

export const AgentsIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 20c0-4 3.6-6 8-6s8 2 8 6" />
  </Icon>
)

export const ScorecardIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 11l2 2 4-4" />
    <rect x="4" y="4" width="16" height="16" rx="2" />
  </Icon>
)

export const SettingsIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
  </Icon>
)

export const UploadIcon = (p: IconProps) => (
  <Icon size={22} {...p}>
    <path d="M12 16V4M6 10l6-6 6 6" />
    <path d="M4 20h16" />
  </Icon>
)

export const SearchIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2} {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="M20 20l-3.5-3.5" />
  </Icon>
)

export const WarnIcon = (p: IconProps) => (
  <Icon size={14} strokeWidth={2.2} {...p}>
    <path d="M12 9v4M12 17h.01" />
    <path d="M10.3 3.9L2.5 18a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
  </Icon>
)

export const BackIcon = (p: IconProps) => (
  <Icon size={14} strokeWidth={2.2} {...p}>
    <path d="M15 5l-7 7 7 7" />
  </Icon>
)

export const CheckIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2.4} {...p}>
    <path d="M5 12l5 5 9-10" />
  </Icon>
)

export const CrossIcon = (p: IconProps) => (
  <Icon size={16} strokeWidth={2.4} {...p}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Icon>
)
