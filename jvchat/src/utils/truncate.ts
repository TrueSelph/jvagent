export function truncate(str: string, length = 100): string {
  if (!str) return ""
  return str.length > length ? str.substring(0, length) + "..." : str
}
