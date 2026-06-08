import { useCallback, useEffect, useRef, useState } from 'react'

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
): { data: T | null; isLoading: boolean } {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  const isFetchingRef = useRef(false)

  fetcherRef.current = fetcher

  const poll = useCallback(async () => {
    if (isFetchingRef.current) return
    isFetchingRef.current = true
    try {
      const result = await fetcherRef.current()
      setData(result)
    } catch {
      // fail-open: keep previous data
    } finally {
      isFetchingRef.current = false
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    void poll()
    const id = setInterval(() => { void poll() }, intervalMs)
    return () => clearInterval(id)
  }, [poll, intervalMs])

  return { data, isLoading }
}
