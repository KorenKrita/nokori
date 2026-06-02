import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchApi } from '@/lib/api'

interface UseApiResult<T> {
  data: T | null
  isLoading: boolean
  isRefetching: boolean
  error: string | null
  refetch: () => Promise<void>
}

export function useApi<T>(path: string, params?: Record<string, string>): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isRefetching, setIsRefetching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const hasLoaded = useRef(false)

  const key = path + JSON.stringify(params ?? {})

  useEffect(() => {
    hasLoaded.current = false
  }, [key])

  const load = useCallback(async (signal?: AbortSignal) => {
    if (hasLoaded.current) {
      setIsRefetching(true)
    } else {
      setIsLoading(true)
    }
    setError(null)
    try {
      const result = await fetchApi<T>(path, params)
      if (signal?.aborted) return
      setData(result)
      hasLoaded.current = true
    } catch (e) {
      if (signal?.aborted) return
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      if (!signal?.aborted) {
        setIsLoading(false)
        setIsRefetching(false)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    const controller = new AbortController()
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(controller.signal)
    return () => { controller.abort() }
  }, [load])

  const refetch = useCallback(async () => { await load() }, [load])

  return { data, isLoading, isRefetching, error, refetch }
}
