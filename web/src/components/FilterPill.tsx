import { HelpTooltip } from '@/components/HelpTooltip'

interface FilterPillProps {
  active: boolean
  label: string
  help?: string
  onClick: () => void
}

const pillStyles = (active: boolean) =>
  `inline-flex items-center rounded-full text-xs font-medium transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
    active
      ? 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]'
      : 'text-text-secondary hover:text-[var(--color-nav-hover-text)] hover:bg-[var(--color-pill-hover-bg)]'
  }`

const labelButtonStyles =
  'border-0 bg-transparent p-0 font-inherit text-inherit cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)] focus-visible:ring-offset-1 rounded-full'

export function FilterPill({ active, label, help, onClick }: FilterPillProps) {
  if (!help) {
    return (
      <button type="button" onClick={onClick} className={`${pillStyles(active)} px-3 py-1.5`}>
        {label}
      </button>
    )
  }

  return (
    <div className={`${pillStyles(active)} gap-1 pl-3 pr-2 py-1.5`}>
      <button type="button" onClick={onClick} className={labelButtonStyles}>
        {label}
      </button>
      <HelpTooltip content={help} />
    </div>
  )
}
