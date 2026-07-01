import { useState, useRef } from "react"
import { useNavigate } from "react-router"
import { Search } from "lucide-react"
import { useDashboard } from "@/api/hooks/useDashboard"
import Shelf from "@/components/media/Shelf"
import ForYouShelf from "@/components/media/ForYouShelf"
import { Skeleton } from "@/components/ui/skeleton"
import { apiFetch } from "@/api/client"
import { usePlayerStore } from "@/store/playerStore"
import ProfileButton from "@/components/profile/ProfileButton"
import type { MediaCardProps } from "@/components/media/MediaCard"
import type { PlaybackEnvelope, ShelfItem } from "@/api/types"

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

function DashboardSkeleton() {
  return (
    <div className="py-3 space-y-2">
      {[1, 2, 3].map((i) => (
        <div key={i} className="space-y-3">
          <div className="px-4 sm:px-6 flex gap-3 items-baseline">
            <Skeleton className="h-5 w-36" />
            <Skeleton className="h-4 w-24" />
          </div>
          <div className="flex gap-1 px-3">
            {[1, 2, 3, 4, 5].map((j) => (
              <div key={j} className="w-44 shrink-0 p-3 space-y-3">
                <Skeleton className="w-full aspect-square rounded-md" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const { data, isLoading, error } = useDashboard(16)
  const playSource = usePlayerStore((s) => s.playSource)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const [query, setQuery] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (q) navigate(`/search?q=${encodeURIComponent(q)}`)
  }

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
      {/* Logo + Search + Profile */}
      <div className="px-4 sm:px-6 flex items-center gap-3">
        <span
          className="text-primary font-bold text-lg tracking-tight select-none shrink-0"
          style={{ fontFamily: "'Onest Variable', sans-serif" }}
        >
          discocs
        </span>
        <form onSubmit={handleSearch} className="flex-1 max-w-xl">
          <div className="relative">
            <Search
              size={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
            />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search artists, releases, tracks…"
              className="w-full rounded-lg bg-muted/50 pl-9 pr-4 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
        </form>
        <div className="ml-auto shrink-0">
          <ProfileButton />
        </div>
      </div>

      {/* For You shelf */}
      <ForYouShelf />

      {/* Shelves */}
      {isLoading && <DashboardSkeleton />}

      {error && (
        <div className="px-4 sm:px-6">
          <p className="text-sm text-destructive">Failed to load dashboard: {error.message}</p>
        </div>
      )}

      {data?.shelves.map((shelf) => (
        <Shelf
          key={shelf.key}
          title={shelf.title}
          shelfKey={shelf.key}
          items={shelf.items.map((item) => shelfItemToCard(item, handlePlayShelfItem))}
        />
      ))}
    </div>
  )
}
