import { describe, expect, it } from "vitest"
import {
  axisLabelStep,
  barHeightPercent,
  formatBucketLabels,
  formatDayHeading,
  groupListensByDay,
  localDayKey,
  parseLocalDate,
} from "./listenHistory"

// Timestamps are built from local wall-clock times, so the local-day
// grouping is checked the same way whatever TZ the test machine runs in.
function at(year: number, month: number, day: number, hour: number, minute = 0): string {
  return new Date(year, month - 1, day, hour, minute).toISOString()
}

describe("groupListensByDay", () => {
  it("splits newest-first listens at local midnight and keeps their order", () => {
    const listens = [
      { listen_id: 4, listened_at: at(2026, 10, 5, 0, 10) },
      { listen_id: 3, listened_at: at(2026, 10, 4, 23, 50) },
      { listen_id: 2, listened_at: at(2026, 10, 4, 8, 0) },
      { listen_id: 1, listened_at: at(2026, 10, 2, 12, 0) },
    ]

    const groups = groupListensByDay(listens)

    expect(groups.map((g) => g.day)).toEqual(["2026-10-05", "2026-10-04", "2026-10-02"])
    expect(groups.map((g) => g.items.map((i) => i.listen_id))).toEqual([[4], [3, 2], [1]])
  })

  it("keeps repeated plays of one track as separate rows of the day", () => {
    const listens = [
      { listen_id: 2, id: 7, listened_at: at(2026, 10, 4, 10, 5) },
      { listen_id: 1, id: 7, listened_at: at(2026, 10, 4, 10, 0) },
    ]
    expect(groupListensByDay(listens)[0].items).toHaveLength(2)
  })

  it("skips unparsable timestamps instead of inventing a day", () => {
    expect(groupListensByDay([{ listened_at: "garbage" }])).toEqual([])
  })
})

describe("parseLocalDate / localDayKey", () => {
  it("reads a server bucket date as that local calendar day (no UTC shift)", () => {
    const date = parseLocalDate("2026-03-01")
    expect(date.getFullYear()).toBe(2026)
    expect(date.getMonth()).toBe(2)
    expect(date.getDate()).toBe(1)
    expect(localDayKey(date)).toBe("2026-03-01")
  })
})

describe("formatDayHeading", () => {
  const now = new Date(2026, 9, 4, 15, 0)

  it("names today and yesterday", () => {
    expect(formatDayHeading("2026-10-04", "en", now)).toBe("Today")
    expect(formatDayHeading("2026-10-03", "en", now)).toBe("Yesterday")
    expect(formatDayHeading("2026-10-04", "ru", now)).toBe("Сегодня")
    expect(formatDayHeading("2026-10-03", "ru", now)).toBe("Вчера")
  })

  it("uses a weekday + date for older days, adding the year only when it differs", () => {
    const sameYear = formatDayHeading("2026-09-28", "en", now)
    expect(sameYear).toContain("Monday")
    expect(sameYear).toContain("28")
    expect(sameYear).not.toContain("2026")
    expect(formatDayHeading("2025-12-31", "en", now)).toContain("2025")
  })
})

describe("barHeightPercent", () => {
  it("is proportional to the largest value", () => {
    expect(barHeightPercent(10, 10)).toBe(100)
    expect(barHeightPercent(5, 10)).toBe(50)
    expect(barHeightPercent(1, 4)).toBe(25)
  })

  it("leaves zero empty but keeps a tiny non-zero value visible", () => {
    expect(barHeightPercent(0, 10)).toBe(0)
    expect(barHeightPercent(0, 0)).toBe(0)
    expect(barHeightPercent(1, 1000)).toBe(2)
  })
})

describe("axisLabelStep", () => {
  it("labels every bar of a short chart and thins out long ones", () => {
    expect(axisLabelStep(6)).toBe(1)
    expect(axisLabelStep(7)).toBe(2)
    expect(axisLabelStep(30)).toBe(5)
    expect(axisLabelStep(0)).toBe(1)
  })
})

describe("formatBucketLabels", () => {
  it("formats a month bucket as month + year, not as its first day", () => {
    const labels = formatBucketLabels("2026-03-01", "month", "en")
    expect(labels.full).toBe("March 2026")
    expect(labels.short).toContain("Mar")
    expect(labels.short).not.toMatch(/\b1\b/)
  })

  it("formats a day bucket with its day of month", () => {
    const labels = formatBucketLabels("2026-03-07", "day", "en")
    expect(labels.full).toContain("7")
    expect(labels.full).toContain("March")
    expect(labels.short).toBe("Mar 7")
  })
})
