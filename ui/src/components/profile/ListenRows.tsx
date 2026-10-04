import { useTranslation } from "react-i18next"
import VirtualTrackRow from "@/components/media/VirtualTrackRow"
import type { ListenItem } from "@/api/profile"
import { formatRelativeTime } from "@/lib/relativeTime"

interface ListenRowsProps {
  readonly listens: readonly ListenItem[]
  readonly sourceLabel?: string
  /** Reference "now" for the relative times (tests). */
  readonly now?: Date
}

/**
 * Listening-history rows: the regular track row, but keyed by listen (a
 * track played twice shows twice) and with "4 min ago" instead of duration.
 */
export default function ListenRows({ listens, sourceLabel, now }: ListenRowsProps) {
  const { i18n } = useTranslation()
  const reference = now ?? new Date()
  const absolute = new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" })
  return (
    <div>
      {listens.map((listen, index) => {
        const at = new Date(listen.listened_at)
        return (
          <VirtualTrackRow
            key={listen.listen_id}
            track={listen}
            index={index}
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
