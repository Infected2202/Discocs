import { describe, expect, it } from "vitest"
import { formatRelativeTime } from "./relativeTime"

// Local-time constructor: the calendar-day logic runs in the test's own zone.
const now = new Date(2026, 9, 4, 12, 0, 0)
const ago = (ms: number) => new Date(now.getTime() - ms).toISOString()
const MIN = 60_000
const HOUR = 60 * MIN

describe("formatRelativeTime", () => {
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" })

  it("reads the last minute (and clock skew) as now", () => {
    expect(formatRelativeTime(ago(20_000), "en", now)).toBe(rtf.format(0, "second"))
    expect(formatRelativeTime(ago(-5_000), "en", now)).toBe(rtf.format(0, "second"))
  })

  it("counts whole minutes and hours", () => {
    expect(formatRelativeTime(ago(4 * MIN + 30_000), "en", now)).toBe(rtf.format(-4, "minute"))
    expect(formatRelativeTime(ago(3 * HOUR + 10 * MIN), "en", now)).toBe(rtf.format(-3, "hour"))
  })

  it("switches to calendar days after 24 hours", () => {
    const yesterday = new Date(2026, 9, 3, 1, 0, 0).toISOString()
    expect(formatRelativeTime(yesterday, "en", now)).toBe(rtf.format(-1, "day"))
    const threeDays = new Date(2026, 9, 1, 18, 0, 0).toISOString()
    expect(formatRelativeTime(threeDays, "en", now)).toBe(rtf.format(-3, "day"))
  })

  it("falls back to a short date after a week, with the year only when it differs", () => {
    const sameYear = new Date(2026, 2, 12, 9, 0, 0)
    expect(formatRelativeTime(sameYear.toISOString(), "en", now)).toBe(
      new Intl.DateTimeFormat("en", { day: "numeric", month: "short" }).format(sameYear),
    )
    const lastYear = new Date(2025, 11, 30, 9, 0, 0)
    expect(formatRelativeTime(lastYear.toISOString(), "en", now)).toBe(
      new Intl.DateTimeFormat("en", { day: "numeric", month: "short", year: "numeric" }).format(lastYear),
    )
  })

  it("localizes", () => {
    const ru = new Intl.RelativeTimeFormat("ru", { numeric: "auto", style: "short" })
    expect(formatRelativeTime(ago(5 * MIN), "ru", now)).toBe(ru.format(-5, "minute"))
  })

  it("returns an empty string for an invalid timestamp", () => {
    expect(formatRelativeTime("not a date", "en", now)).toBe("")
  })
})
