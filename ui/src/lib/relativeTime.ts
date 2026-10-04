// "4 мин назад" / "вчера" / "12 мар." — relative listen times (social features:
// the History shelf and the profile's recent listens).

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/**
 * Format a past ISO timestamp relative to `now` in `locale`:
 * under a minute → "now", minutes, hours (same or previous day by elapsed
 * time), calendar days up to a week ("yesterday", "3 days ago"), then a short
 * date (with the year only when it differs from `now`'s). Returns "" for an
 * unparsable value; future timestamps (clock skew) read as "now".
 */
export function formatRelativeTime(iso: string, locale: string, now: Date = new Date()): string {
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return ""
  const elapsed = now.getTime() - then.getTime()
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" })
  if (elapsed < MINUTE) return rtf.format(0, "second")
  if (elapsed < HOUR) return rtf.format(-Math.floor(elapsed / MINUTE), "minute")
  if (elapsed < DAY) return rtf.format(-Math.floor(elapsed / HOUR), "hour")
  const days = Math.round((startOfDay(now) - startOfDay(then)) / DAY)
  if (days < 7) return rtf.format(-Math.max(days, 1), "day")
  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    ...(then.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  }).format(then)
}
