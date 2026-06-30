import { useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import TrackTable from "./TrackTable"
import type { ArtistTopTrack } from "@/api/types"

const PAGE_SIZE = 5

interface PopularTracksProps {
  tracks: ArtistTopTrack[]
  sourceLabel?: string
}

export default function PopularTracks({ tracks, sourceLabel }: PopularTracksProps) {
  const [page, setPage] = useState(0)
  const [animKey, setAnimKey] = useState(0)
  const [direction, setDirection] = useState<"left" | "right">("right")

  if (tracks.length === 0) return null

  const totalPages = Math.ceil(tracks.length / PAGE_SIZE)
  const visible = tracks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const canPrev = page > 0
  const canNext = page < totalPages - 1

  function goTo(next: number, dir: "left" | "right") {
    setDirection(dir)
    setPage(next)
    setAnimKey((k) => k + 1)
  }

  return (
    <section className="space-y-0 overflow-x-clip">
      {totalPages > 1 && (
        <div className="px-4 sm:px-6 flex items-center justify-end gap-2 pb-1">
          <button
            onClick={() => canPrev && goTo(page - 1, "left")}
            className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canPrev ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
            aria-label="Previous"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            onClick={() => canNext && goTo(page + 1, "right")}
            className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canNext ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
            aria-label="Next"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}
      <div
        key={animKey}
        className={`px-4 sm:px-6 ${direction === "right" ? "tracks-slide-right" : "tracks-slide-left"}`}
      >
        <TrackTable
          tracks={visible}
          showRelease
          sourceLabel={sourceLabel}
          indexOffset={page * PAGE_SIZE}
        />
      </div>
    </section>
  )
}
