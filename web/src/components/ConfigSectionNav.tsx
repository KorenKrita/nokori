import { cn } from '@/lib/utils'

interface Section {
  id: string
  label: string
}

interface Props {
  sections: Section[]
  activeId: string | null
  onSelect: (id: string) => void
}

export function ConfigSectionNav({ sections, activeId, onSelect }: Props) {
  return (
    <nav
      className="sticky top-0 z-10 flex gap-2 overflow-x-auto py-2 px-1 -mx-1 scrollbar-thin bg-[var(--color-bg-base)]/95 backdrop-blur-xl border-b border-[var(--color-border-subtle)]"
      aria-label="Config sections"
    >
      {sections.map((section) => (
        <button
          key={section.id}
          type="button"
          onClick={() => onSelect(section.id)}
          className={cn(
            'shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]',
            activeId === section.id
              ? 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]'
              : 'text-text-secondary hover:text-[var(--color-nav-hover-text)] hover:bg-[var(--color-pill-hover-bg)]',
          )}
        >
          {section.label}
        </button>
      ))}
    </nav>
  )
}
