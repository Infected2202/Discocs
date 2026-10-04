// Pure helpers of the profile page: local-day grouping of the listening
// history and the axis/label formatting of its div-bar charts.

const DAY = 24 * 60 * 60 * 1000

function pad2(value: number): string {
  return String(value).padStart(2, "0")
}

/** `YYYY-MM-DD` of a Date in the browser's local zone (the viewer's tz). */
export function localDayKey(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

/**
 * A server bucket date (`YYYY-MM-DD`, already local to the viewer's tz) as a
 * local Date at midnight. `new Date("2026-10-04")` would parse it as UTC
 * midnight and shift it a day back west of Greenwich.
 */
export function parseLocalDate(date: string): Date {
  const [year, month, day] = date.split("-").map(Number)
  return new Date(year, (month || 1) - 1, day || 1)
}

export interface DayGroup<T> {
  /** Local `YYYY-MM-DD`. */
  day: string
  items: T[]
}

/**
 * Split newest-first listens into consecutive local-day groups, keeping the
 * order. Items with an unparsable timestamp are skipped.
 */
export function groupListensByDay<T extends { listened_at: string }>(items: readonly T[]): DayGroup<T>[] {
  const groups: DayGroup<T>[] = []
  for (const item of items) {
    const at = new Date(item.listened_at)
    if (Number.isNaN(at.getTime())) continue
    const day = localDayKey(at)
    const last = groups.at(-1)
    if (last?.day === day) last.items.push(item)
    else groups.push({ day, items: [item] })
  }
  return groups
}

function capitalize(text: string, locale: string): string {
  return text.charAt(0).toLocaleUpperCase(locale) + text.slice(1)
}

/** Day group heading: "Today" / "Yesterday" / "Monday, 28 September" (+ year if not this year). */
export function formatDayHeading(day: string, locale: string, now: Date = new Date()): string {
  const date = parseLocalDate(day)
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const daysAgo = Math.round((today.getTime() - date.getTime()) / DAY)
  if (daysAgo === 0 || daysAgo === 1) {
    const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" })
    return capitalize(rtf.format(daysAgo === 0 ? 0 : -1, "day"), locale)
  }
  return capitalize(
    new Intl.DateTimeFormat(locale, {
      weekday: "long",
      day: "numeric",
      month: "long",
      ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
    }).format(date),
    locale,
  )
}

/**
 * Bar height in % of the chart: proportional to the largest value; a
 * non-zero value never drops below a visible sliver, zero stays empty.
 */
export function barHeightPercent(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0
  return Math.max(2, Math.round((value / max) * 1000) / 10)
}

/** Show an axis label on every `step`-th bar so roughly `target` labels fit. */
export function axisLabelStep(count: number, target = 6): number {
  return Math.max(1, Math.ceil(count / target))
}

/** Full (tooltip) and short (axis) labels of a by-day bucket. */
export function formatBucketLabels(
  date: string,
  bucket: "day" | "month",
  locale: string,
): { full: string; short: string } {
  const value = parseLocalDate(date)
  if (bucket === "month") {
    return {
      full: new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" }).format(value),
      short: new Intl.DateTimeFormat(locale, { month: "short", year: "2-digit" }).format(value),
    }
  }
  return {
    full: new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "long", year: "numeric" }).format(value),
    short: new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" }).format(value),
  }
}

/**
 * Drop the listen of the play that is still going on. A listen is recorded
 * mid-track (play threshold), so while the track keeps playing it would show
 * twice: as the "now" row and as "2 min ago". Only the newest listen can be
 * that play — same track, listened no longer ago than the track lasts.
 */
export function withoutCurrentPlay<T extends { id: number; listened_at: string }>(
  listens: readonly T[],
  playing: { id: number; duration: number | null } | null | undefined,
  now: Date = new Date(),
): T[] {
  const [first, ...rest] = listens
  if (!playing || !first || first.id !== playing.id) return [...listens]
  const elapsed = now.getTime() - new Date(first.listened_at).getTime()
  const window = ((playing.duration ?? 0) + 60) * 1000
  return elapsed >= 0 && elapsed <= window ? rest : [...listens]
}
