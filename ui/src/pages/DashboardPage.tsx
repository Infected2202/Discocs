import { useTranslation } from "react-i18next"
import { useDashboard } from "@/api/hooks/useDashboard"
import Shelf from "@/components/media/Shelf"
import ForYouShelf from "@/components/media/ForYouShelf"
import PeopleShelf from "@/components/media/PeopleShelf"
import ShelfSkeleton from "@/components/media/ShelfSkeleton"
import { apiFetch } from "@/api/client"
import { usePlayerStore } from "@/store/playerStore"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"
import { SHELF_PREVIEW_LIMIT } from "@/lib/shelves"
import type { PlaybackEnvelope, ShelfItem } from "@/api/types"

function DashboardSkeleton() {
  return (
    <div className="py-3 space-y-2">
      <ShelfSkeleton rows={3} />
    </div>
  )
}

export default function DashboardPage() {
  const { t, i18n } = useTranslation("dashboard")
  const { data, isLoading, error } = useDashboard(SHELF_PREVIEW_LIMIT)
  const playSource = usePlayerStore((s) => s.playSource)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  async function handlePlayShelfItem(item: ShelfItem) {
    const pa = item.play_action
    if (!pa) return
    if (pa.type === "post") {
      try {
        const envelope = await apiFetch<PlaybackEnvelope>(pa.endpoint, { method: "POST" })
        await playFromEnvelope(envelope)
      } catch {
        // silently ignore
      }
    } else {
      playSource(pa.source_type, Number(pa.source_id), pa.source_label ?? item.title)
    }
  }

  return (
    <div className="py-3 space-y-2">

      {/* People: other discocs users and what they play right now */}
      <PeopleShelf />

      {/* For You shelf */}
      <ForYouShelf />

      {/* Shelves */}
      {isLoading && <DashboardSkeleton />}

      {error && (
        <div className="px-4 sm:px-6">
          <p className="text-sm text-destructive">{t("loadError", { message: error.message })}</p>
        </div>
      )}

      {data?.shelves.map((shelf) => (
        <Shelf
          key={shelf.key}
          title={t(`shelves.${shelf.key}`, { defaultValue: shelf.title })}
          shelfKey={shelf.key}
          total={shelf.total}
          items={shelf.items.map((item) => shelfItemToCard(item, handlePlayShelfItem, t, i18n.language))}
        />
      ))}
    </div>
  )
}
