import { useEffect, type RefObject } from 'react'

export function useSectionObserver(
  sectionIds: string[],
  refs: RefObject<Record<string, HTMLElement | null>>,
  onActive: (id: string) => void,
  enabled = true,
) {
  useEffect(() => {
    if (!enabled || sectionIds.length === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => {
            const ra = a.intersectionRatio
            const rb = b.intersectionRatio
            if (rb !== ra) return rb - ra
            return a.boundingClientRect.top - b.boundingClientRect.top
          })
        const top = visible[0]
        if (!top?.target.id) return
        const id = top.target.id.replace(/^config-section-/, '')
        if (id) onActive(id)
      },
      { rootMargin: '-12% 0px -55% 0px', threshold: [0, 0.1, 0.35, 0.6] },
    )

    for (const id of sectionIds) {
      const el = refs.current?.[id]
      if (el) observer.observe(el)
    }

    return () => observer.disconnect()
  }, [sectionIds, refs, onActive, enabled])
}
