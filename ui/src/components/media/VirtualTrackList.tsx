import { useVirtualizer } from "@tanstack/react-virtual"
import { useScrollRef } from "@/contexts/ScrollContext"
import VirtualTrackRow from "./VirtualTrackRow"
import type { TrackSummary } from "@/api/types"

const ROW_HEIGHT = 52

interface VirtualTrackListProps {
  tracks: TrackSummary[]
  showArtwork?: boolean
  showRelease?: boolean
  sourceLabel?: string
}

export default function VirtualTrackList({
  tracks,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
}: VirtualTrackListProps) {
  const scrollRef = useScrollRef()

  const rowVirtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  })

  const totalSize = rowVirtualizer.getTotalSize()
  const virtualItems = rowVirtualizer.getVirtualItems()

  return (
    <div style={{ height: totalSize, position: "relative" }}>
      {virtualItems.map((virtualRow) => (
        <div
          key={virtualRow.key}
          data-index={virtualRow.index}
          ref={rowVirtualizer.measureElement}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            transform: `translateY(${virtualRow.start}px)`,
          }}
        >
          <VirtualTrackRow
            track={tracks[virtualRow.index]}
            index={virtualRow.index}
            showArtwork={showArtwork}
            showRelease={showRelease}
            sourceLabel={sourceLabel}
          />
        </div>
      ))}
    </div>
  )
}
