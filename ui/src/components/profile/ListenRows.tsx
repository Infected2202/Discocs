import { useTranslation } from "react-i18next"
import VirtualTrackRow from "@/components/media/VirtualTrackRow"
import type { ListenItem } from "@/api/profile"
import type { TrackSummary } from "@/api/types"
import { formatRelativeTime } from "@/lib/relativeTime"

interface ListenRowsProps {
  readonly listens: readonly ListenItem[]
  /** The track playing right now: shown first, as "now" (Last.fm-style). */
  readonly nowPlaying?: TrackSummary | null
  readonly sourceLabel?: string
  /** Reference "now" for the relative times (tests). */
  readonly now?: Date
}

/**
 * Listening-history rows: the regular track row, but keyed by listen (a
 * track played twice shows twice) and with "4 min ago" instead of duration.
 */
export default function ListenRows({ listens, nowPlaying, sourceLabel, now }: ListenRowsProps) {
  const { t, i18n } = useTranslation("user")
  const reference = now ?? new Date()
  const absolute = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" })
  const offset = nowPlaying ? 1 : 0
  return (
    <div>
      {nowPlaying && (
        <VirtualTrackRow
          key="now"
          track={nowPlaying}
          index={0}
          metricWide
          sourceLabel={sourceLabel}
          metric={
            <span className="inline-flex items-center gap-1.5 text-foreground" data-testid="listen-now">
              <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-green-500 motion-safe:animate-pulse" />
              {t("now")}
            </span>
          }
        />
      )}
      {listens.map((listen, index) => {
        const at = new Date(listen.listened_at)
        return (
          <VirtualTrackRow
            key={listen.listen_id}
            track={listen}
            index={index + offset}
            metricWide
            sourceLabel={sourceLabel}
            metric={
              <time
                dateTime={listen.listened_at}
                title={Number.isNaN(at.getTime()) ? undefined : absolute.format(at)}
              >
                {formatRelativeTime(listen.listened_at, i18n.language, reference)}
              </time>
            }
          />
        )
      })}
    </div>
  )
}
