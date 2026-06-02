import type { KeyboardEvent, ReactNode } from 'react'

interface HelpTooltipProps {
  content: ReactNode
  className?: string
}

function stopActivationKeys(e: KeyboardEvent) {
  if (e.key === ' ' || e.key === 'Enter') {
    e.preventDefault()
    e.stopPropagation()
  }
}

/** Small “?” control; hover or focus shows a brief explanation below the icon. */
export function HelpTooltip({ content, className = '' }: HelpTooltipProps) {
  return (
    <span
      className={`relative inline-flex shrink-0 group/help ${className}`}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <span
        role="button"
        tabIndex={0}
        aria-label="help"
        onKeyDown={stopActivationKeys}
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-text-tertiary/60 text-[9px] leading-none text-text-tertiary cursor-help hover:border-accent-sky/60 hover:text-accent-sky transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]"
      >
        ?
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-[calc(100%+8px)] z-[200] w-72 max-w-[min(18rem,calc(100vw-3rem))] -translate-x-1/2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-base)] px-3 py-2.5 text-[11px] leading-relaxed text-[var(--color-text-primary)] opacity-0 shadow-2xl invisible delay-150 transition-[opacity,visibility] duration-150 ease-out group-hover/help:opacity-100 group-hover/help:visible group-hover/help:delay-0 group-focus-within/help:opacity-100 group-focus-within/help:visible group-focus-within/help:delay-0"
      >
        {content}
      </span>
    </span>
  )
}
