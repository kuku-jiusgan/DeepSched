import type { Dayjs } from 'dayjs'

export interface PlannedRange {
  start: Dayjs
  end: Dayjs
}

export function buildTrailingDelayRanges(
  plannedRanges: PlannedRange[],
  durationMinutes: number,
): Array<[Dayjs, Dayjs]> {
  const ranges: Array<[Dayjs, Dayjs]> = []
  let remainingMinutes = Math.max(0, Math.round(durationMinutes))
  const orderedRanges = plannedRanges
    .filter(range => range.end.isAfter(range.start))
    .sort((left, right) => right.end.valueOf() - left.end.valueOf())

  for (const range of orderedRanges) {
    if (remainingMinutes <= 0) break
    const availableMinutes = Math.ceil(range.end.diff(range.start, 'minute', true))
    const consumedMinutes = Math.min(remainingMinutes, availableMinutes)
    const rangeStart = consumedMinutes >= availableMinutes
      ? range.start
      : range.end.subtract(consumedMinutes, 'minute')
    ranges.unshift([rangeStart, range.end])
    remainingMinutes -= consumedMinutes
  }

  return ranges
}
