import { useTranslation } from "react-i18next"
import type { UserProfile } from "@/api/profile"
import { axisLabelStep, barHeightPercent, formatBucketLabels } from "@/lib/listenHistory"

export interface ChartBar {
  readonly key: string
  /** Accessible name and tooltip, value included ("5 Oct: 7 listens"). */
  readonly label: string
  readonly value: number
  /** Text under the bar; empty for bars between labelled ones. */
  readonly axisLabel?: string
}

interface BarChartProps {
  readonly title: string
  readonly bars: readonly ChartBar[]
}

/** Vertical bars built from plain divs, heights relative to the largest value. */
export function BarChart({ title, bars }: BarChartProps) {
  const max = bars.reduce((acc, bar) => Math.max(acc, bar.value), 0)
  return (
    <figure className="min-w-0 space-y-1">
      <figcaption className="text-xs text-muted-foreground">{title}</figcaption>
      <div role="list" aria-label={title} className="flex h-28 items-end gap-px">
        {bars.map((bar) => (
          <div
            key={bar.key}
            role="listitem"
            aria-label={bar.label}
            title={bar.label}
            className="group/bar flex h-full min-w-0 flex-1 items-end"
          >
            <div
              data-testid="chart-bar"
              className="w-full rounded-t-[2px] bg-primary/60 transition-colors group-hover/bar:bg-primary"
              style={{ height: `${barHeightPercent(bar.value, max)}%` }}
            />
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="flex gap-px">
        {bars.map((bar) => (
          <div key={bar.key} className="relative h-3 min-w-0 flex-1">
            {bar.axisLabel && (
              <span className="absolute left-0 top-0 whitespace-nowrap text-[10px] leading-3 text-muted-foreground">
                {bar.axisLabel}
              </span>
            )}
          </div>
        ))}
      </div>
    </figure>
  )
}

interface ShareRow {
  readonly key: string
  readonly label: string
  readonly hint?: string
  readonly share: number
}

function ShareList({ title, rows, locale }: { readonly title: string; readonly rows: readonly ShareRow[]; readonly locale: string }) {
  const percent = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 })
  if (rows.length === 0) return null
  return (
    <div className="min-w-0 space-y-1.5">
      <p className="text-xs text-muted-foreground">{title}</p>
      <ul className="space-y-1">
        {rows.map((row) => (
          <li
            key={row.key}
            className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_3rem] items-center gap-2 text-xs"
            title={`${row.hint ? `${row.hint} · ` : ""}${row.label}: ${percent.format(row.share)}`}
          >
            <span className="truncate">{row.label}</span>
            <span className="h-1.5 rounded-full bg-muted" aria-hidden="true">
              <span
                className="block h-full rounded-full bg-primary/70"
                style={{ width: `${Math.min(100, Math.max(0, row.share * 100))}%` }}
              />
            </span>
            <span className="text-right tabular-nums text-muted-foreground">{percent.format(row.share)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Period block of the profile: summary numbers, by-day and by-hour bars, sound profile. */
export default function ProfileStats({ profile }: { readonly profile: UserProfile }) {
  const { t, i18n } = useTranslation("user")
  const locale = i18n.language
  const number = new Intl.NumberFormat(locale)
  const hours = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 })
  const listens = (count: number) => t("listenCount", { count, formatted: number.format(count) })

  const bucket = profile.by_day_bucket
  const dayStep = axisLabelStep(profile.by_day.length)
  const dayBars: ChartBar[] = profile.by_day.map((entry, index) => {
    const labels = formatBucketLabels(entry.date, bucket, locale)
    return {
      key: entry.date,
      label: `${labels.full}: ${listens(entry.listens)}`,
      value: entry.listens,
      axisLabel: index % dayStep === 0 ? labels.short : undefined,
    }
  })
  const hourBars: ChartBar[] = profile.by_hour.map((value, hour) => ({
    key: String(hour),
    label: `${t("charts.hour", { hour })}: ${listens(value)}`,
    value,
    axisLabel: hour % 6 === 0 ? t("charts.hour", { hour }) : undefined,
  }))

  const tiles = [
    { key: "listens", label: t("summary.listens"), value: number.format(profile.summary.listens) },
    { key: "hours", label: t("summary.hours"), value: hours.format(profile.summary.hours) },
    { key: "artists", label: t("summary.artists"), value: number.format(profile.summary.artists) },
  ]

  return (
    <div className="px-4 sm:px-6 space-y-6">
      <dl className="grid grid-cols-3 gap-3 max-w-md">
        {tiles.map((tile) => (
          <div key={tile.key} className="min-w-0">
            <dt className="text-xs text-muted-foreground truncate">{tile.label}</dt>
            <dd className="text-2xl font-semibold tabular-nums" data-testid={`summary-${tile.key}`}>{tile.value}</dd>
          </div>
        ))}
      </dl>

      <div className="grid gap-6 md:grid-cols-2">
        {dayBars.length > 0 && (
          <BarChart title={bucket === "month" ? t("charts.byMonth") : t("charts.byDay")} bars={dayBars} />
        )}
        <BarChart title={t("charts.byHour")} bars={hourBars} />
      </div>

      {(profile.sound.genres.length > 0 || profile.sound.moods.length > 0) && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium">{t("sound.title")}</h3>
          <div className="grid gap-6 md:grid-cols-2">
            <ShareList
              title={t("sound.genres")}
              locale={locale}
              rows={profile.sound.genres.map((g) => ({
                key: g.label,
                label: g.style || g.label,
                hint: g.style ? g.genre : undefined,
                share: g.share,
              }))}
            />
            <ShareList
              title={t("sound.moods")}
              locale={locale}
              rows={profile.sound.moods.map((m) => ({ key: m.label, label: m.label, share: m.share }))}
            />
          </div>
        </div>
      )}
    </div>
  )
}
