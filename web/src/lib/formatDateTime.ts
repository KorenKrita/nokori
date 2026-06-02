/** Format ISO timestamps for display (no T/Z). Returns input unchanged if not parseable. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const trimmed = iso.trim()
  if (!trimmed || trimmed === 'never') return trimmed

  const d = new Date(trimmed)
  if (Number.isNaN(d.getTime())) return trimmed

  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
