import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePolling } from './usePolling'

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('usePolling', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads data on mount and clears loading', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true })
    const { result } = renderHook(() => usePolling(fetcher, 1000))

    expect(result.current.isLoading).toBe(true)
    await flushMicrotasks()
    expect(result.current.isLoading).toBe(false)
    expect(result.current.data).toEqual({ ok: true })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('polls on interval', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockResolvedValueOnce({ n: 2 })
    const { result } = renderHook(() => usePolling(fetcher, 500))

    await flushMicrotasks()
    expect(result.current.data).toEqual({ n: 1 })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    await flushMicrotasks()
    expect(result.current.data).toEqual({ n: 2 })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('keeps previous data when a poll fails', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce({ n: 1 })
      .mockRejectedValueOnce(new Error('boom'))
    const { result } = renderHook(() => usePolling(fetcher, 500))

    await flushMicrotasks()
    expect(result.current.data).toEqual({ n: 1 })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    await flushMicrotasks()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(result.current.data).toEqual({ n: 1 })
    expect(result.current.isLoading).toBe(false)
  })
})
