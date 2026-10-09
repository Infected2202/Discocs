import type { TrackCredit } from "@/api/types"

/** A piece of a track title: plain text, or a featured artist / remixer name. */
export interface TitleSegment {
  text: string
  credit?: TrackCredit
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

/**
 * Splits a title into plain text and the credited names written in it
 * ("Pi Pu Pa (ft. RLGN)" → "Pi Pu Pa (ft. ", RLGN, ")"). A name matches as a
 * whole word, case-insensitively; when it occurs more than once the last
 * occurrence wins — credits sit at the end ("Space (Space Remix)"). Names not
 * found in the title (a remixer only from the REMIXER tag) are skipped.
 */
export function splitTitleByCredits(
  title: string,
  credits: readonly TrackCredit[] | undefined,
): TitleSegment[] {
  if (!credits?.length) return [{ text: title }]
  const ranges: { start: number; end: number; credit: TrackCredit }[] = []
  const overlaps = (start: number, end: number) =>
    ranges.some((range) => start < range.end && range.start < end)
  // Longer names first: "Ray Keith & Nookie" before "Nookie".
  for (const credit of [...credits].sort((a, b) => b.text.length - a.text.length)) {
    if (!credit.text) continue
    const pattern = new RegExp(`(?<![\\p{L}\\p{N}])${escapeRegExp(credit.text)}(?![\\p{L}\\p{N}])`, "giu")
    const free = [...title.matchAll(pattern)]
      .map((match) => ({ start: match.index, end: match.index + match[0].length }))
      .filter(({ start, end }) => !overlaps(start, end))
    const last = free.at(-1)
    if (last) ranges.push({ ...last, credit })
  }
  if (ranges.length === 0) return [{ text: title }]
  ranges.sort((a, b) => a.start - b.start)
  const segments: TitleSegment[] = []
  let cursor = 0
  for (const range of ranges) {
    if (range.start > cursor) segments.push({ text: title.slice(cursor, range.start) })
    segments.push({ text: title.slice(range.start, range.end), credit: range.credit })
    cursor = range.end
  }
  if (cursor < title.length) segments.push({ text: title.slice(cursor) })
  return segments
}
