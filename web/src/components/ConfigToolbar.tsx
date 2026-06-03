import { MagnifyingGlassIcon, FunnelIcon } from '@phosphor-icons/react'
import { cn } from '@/lib/utils'
import { t } from '@/lib/i18n'

interface Props {
  search: string
  onSearchChange: (v: string) => void
  onlySetInFile: boolean
  onOnlySetInFileChange: (v: boolean) => void
}

export function ConfigToolbar({ search, onSearchChange, onlySetInFile, onOnlySetInFileChange }: Props) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="relative flex-1 max-w-md">
        <MagnifyingGlassIcon
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none"
        />
        <input
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={t('config.search_placeholder')}
          className="w-full rounded-full pl-9 pr-3 py-2 text-sm bg-[var(--color-input-bg)] border border-[var(--color-input-border)] text-[var(--color-text-primary)] placeholder:text-text-tertiary focus:outline-none focus:ring-2 focus:ring-[var(--color-border-focus)]"
        />
      </div>
      <button
        type="button"
        onClick={() => onOnlySetInFileChange(!onlySetInFile)}
        className={cn(
          'inline-flex items-center gap-2 px-3 py-2 rounded-full text-xs font-medium transition-all duration-300 shrink-0',
          onlySetInFile
            ? 'bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)]'
            : 'text-text-secondary hover:bg-[var(--color-pill-hover-bg)]',
        )}
      >
        <FunnelIcon size={14} weight={onlySetInFile ? 'fill' : 'regular'} />
        {t('config.filter_in_file')}
      </button>
    </div>
  )
}
