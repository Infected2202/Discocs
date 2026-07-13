import { useEffect, useRef } from "react"
import { useParams, useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { ChevronLeft } from "lucide-react"
import { useShelf } from "@/api/hooks/useShelf"
import { usePlayerStore } from "@/store/playerStore"
import { apiFetch } from "@/api/client"
import MediaCard from "@/components/media/MediaCard"
import VirtualCardGrid from "@/components/media/VirtualCardGrid"
import { Skeleton } from "@/components/ui/skeleton"
import type { MediaCardProps } from "@/components/media/MediaCard"
import type { ShelfItem, PlaybackEnvelope } from "@/api/types"

function shelfItemToCard(item: ShelfItem, onPlay: (item: ShelfItem) => void): MediaCardProps {
  return {
    id: item.entity_id,
    type: item.entity_type,
    title: item.title,
    subtitle: item.subtitle,
    subtitleLinks: item.subtitle_links,
    href: item.action?.target,
    reason: item.reason,
    artwork: item.artwork,
    onPlay: item.play_action ? () => onPlay(item) : undefined,
  }
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-1 px-3">
      {Array.from({ length: 16 }).map((_, i) => (
        <div key={i} className="p-3 space-y-3">
          <Skeleton className="w-full aspect-square rounded-md" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  )
}

export default function ShelfPage() {
  const { t } = useTranslation("media")
  const { key = "" } = useParams<{ key: string }>()
  const navigate = useNavigate()
  const playSource = usePlayerStore((s) => s.playSource)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const sentinelRef = useRef<HTMLDivElement>(null)

  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } = useShelf(key)

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

  // Infinite scroll sentinel
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { rootMargin: "200px" },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const firstPage = data?.pages[0]
  const title = t(`shelves.${key}`, { ns: "dashboard", defaultValue: firstPage?.title ?? key })
  const allItems = data?.pages.flatMap((p) => p.items) ?? []

  return (
    <div className="py-6 space-y-6">
      {/* Header */}
      <div className="px-4 sm:px-6 flex items-center gap-3">
        <button
          onClick={() => navigate(-1)}
          className="flex items-center justify-center w-8 h-8 rounded-full hover:bg-muted transition-colors"
          aria-label={t("actions.back", { ns: "common" })}
        >
          <ChevronLeft size={18} />
        </button>
        <h1 className="text-xl font-semibold">{title}</h1>
        {firstPage && (
          <span className="text-sm text-muted-foreground">{t("itemCount", { count: firstPage.total })}</span>
        )}
      </div>

      {/* Grid — virtualized so only visible cards (and their images) stay in DOM */}
      {isLoading ? (
        <GridSkeleton />
      ) : (
        <div className="px-3">
          <VirtualCardGrid
            items={allItems as ShelfItem[]}
            getKey={(item) => `${item.entity_type}-${item.entity_id}`}
            renderItem={(item) => (
              <MediaCard
                {...shelfItemToCard(item, handlePlayShelfItem)}
                variant="shelf"
                className="w-full"
              />
            )}
          />
        </div>
      )}

      {/* Infinite scroll sentinel */}
      <div ref={sentinelRef} className="h-4" />

      {isFetchingNextPage && (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-1 px-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="p-3 space-y-3">
              <Skeleton className="w-full aspect-square rounded-md" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
