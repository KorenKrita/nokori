export const SOURCE_COLORS: Record<string, string> = {
  session_start: 'text-sky-400',
  user_prompt_submit: 'text-emerald-400',
  pre_tool_use: 'text-violet-400',
  session_end: 'text-zinc-400',
  cold_pipeline: 'text-amber-400',
  cli_extract: 'text-orange-400',
  cli_add: 'text-orange-400',
  cli_dismiss: 'text-orange-400',
  maintenance: 'text-teal-400',
}

export function getSourceColor(source: string): string {
  return SOURCE_COLORS[source] ?? 'text-text-secondary'
}
