import { Component, type ReactNode } from 'react'
import { t } from '@/lib/i18n'

interface Props { children: ReactNode }
interface State { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center min-h-[50vh] gap-4 text-center px-4">
          <p className="text-sm text-text-secondary">
            {this.state.error.message?.includes('Loading chunk')
              ? t('error.chunk_failed')
              : t('error.generic')}
          </p>
          <button
            onClick={() => window.location.reload()}
            className="px-4 py-2 rounded-full bg-[var(--color-pill-active-bg)] text-[var(--color-pill-active-text)] text-sm font-medium"
          >
            {t('error.reload')}
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
