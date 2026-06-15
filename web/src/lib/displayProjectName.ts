const HASH_SUFFIX_RE = /-[a-f0-9]{8}$/

export function displayProjectName(projectId: string): string {
  return HASH_SUFFIX_RE.test(projectId) ? projectId.slice(0, -9) : projectId
}
