import { describe, expect, it } from 'vitest'
import dayjs from 'dayjs'
import { buildTrailingDelayRanges } from './instrumentGanttDelayRanges'

describe('buildTrailingDelayRanges', () => {
  it('keeps the delay range attached to the current task end after rescheduling', () => {
    const slots = [
      ['2026-08-12T16:00:00', '2026-08-12T20:00:00'],
      ['2026-08-17T08:30:00', '2026-08-17T20:00:00'],
      ['2026-08-18T08:30:00', '2026-08-18T20:00:00'],
      ['2026-08-19T08:30:00', '2026-08-19T20:00:00'],
      ['2026-08-20T08:30:00', '2026-08-20T16:30:00'],
    ].map(([start, end]) => ({ start: dayjs(start), end: dayjs(end) }))

    const ranges = buildTrailingDelayRanges(slots, 69 * 60)

    expect(ranges[ranges.length - 1][1].format('YYYY-MM-DD HH:mm')).toBe('2026-08-20 16:30')
    expect(ranges[0][0].format('YYYY-MM-DD HH:mm')).toBe('2026-08-12 16:00')
    expect(ranges.reduce(
      (total, [start, end]) => total + end.diff(start, 'minute'),
      0,
    )).toBe(46.5 * 60)
  })

  it('marks only the trailing part when delay is shorter than planned duration', () => {
    const ranges = buildTrailingDelayRanges([
      { start: dayjs('2026-08-14T18:00:00'), end: dayjs('2026-08-14T20:00:00') },
      { start: dayjs('2026-08-17T08:30:00'), end: dayjs('2026-08-17T10:30:00') },
    ], 3 * 60)

    expect(ranges.map(([start, end]) => [
      start.format('YYYY-MM-DD HH:mm'),
      end.format('YYYY-MM-DD HH:mm'),
    ])).toEqual([
      ['2026-08-14 19:00', '2026-08-14 20:00'],
      ['2026-08-17 08:30', '2026-08-17 10:30'],
    ])
  })
})
