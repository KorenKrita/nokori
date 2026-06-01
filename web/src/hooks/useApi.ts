import { useCallback, useEffect, useState } from 'react'
import { fetchApi } from '@/lib/api'

interface UseApiResult<T> {
  data: T | null
  isLoading: boolean
  error: string | null
  refetch: () => void
}

export function useApi<T>(path: string, params?: Record<string, string>): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const key = path + JSON.stringify(params ?? {})

  const load = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const result = await fetchApi<T>(path, params)
      setData(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setIsLoading(false)
    }
  }, [key])

  useEffect(() => { load() }, [load])

  return { data, isLoading, error, refetch: load }
}
