import TrackRow from "./TrackRow"
import type { TrackSummary, ReleaseTrackItem } from "@/api/types"

interface TrackTableProps {
  tracks: (TrackSummary | ReleaseTrackItem)[]
  showArtwork?: boolean
  showRelease?: boolean
  sourceLabel?: string
  releaseContextId?: number
}

export default function TrackTable({
  tracks,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
  releaseContextId,
}: TrackTableProps) {
  if (tracks.length === 0) return null

  return (
    <table className="w-full table-fixed border-collapse">
      <colgroup>
        <col className="w-10" />
        {showArtwork && <col className="w-12" />}
        <col />
        {showRelease && <col className="hidden md:table-column w-[180px]" />}
        <col className="w-14" />
        <col className="w-8" />
      </colgroup>
      <tbody>
        {tracks.map((track, i) => (
          <TrackRow
            key={track.id}
            track={track}
            index={i}
            showArtwork={showArtwork}
            showRelease={showRelease}
            sourceLabel={sourceLabel}
            releaseContextId={releaseContextId}
          />
        ))}
      </tbody>
    </table>
  )
}
