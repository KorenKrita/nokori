import { useEffect } from 'react'

export function useHotkey(
  key: string,
  handler: (e: KeyboardEvent) => void,
  options?: { metaOrCtrl?: boolean; enabled?: boolean },
) {
  const { metaOrCtrl = true, enabled = true } = options ?? {}

  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (metaOrCtrl && !(e.metaKey || e.ctrlKey)) return
      if (e.key.toLowerCase() !== key.toLowerCase()) return
      handler(e)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [key, handler, metaOrCtrl, enabled])
}
