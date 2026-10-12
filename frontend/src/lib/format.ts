export function shortSha(sha: string | null | undefined) {
  return sha ? sha.slice(0, 10) : "—"
}

export function seconds(value: number | null) {
  if (value == null) return "—"
  if (value < 10) return `${value.toFixed(1)}s`
  return `${Math.round(value)}s`
}

export function percent(value: number | null) {
  if (value == null) return "—"
  return `${Math.round(value * 100)}%`
}

export function money(value: number | null | undefined) {
  if (value == null) return "—"
  return `$${value.toFixed(4)}`
}

export function titleCase(value: string) {
  return value.replaceAll("_", " ")
}
