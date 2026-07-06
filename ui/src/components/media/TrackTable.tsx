import TrackRow from "./TrackRow"
import type { TrackSummary, ReleaseTrackItem, ArtistTopTrack } from "@/api/types"

interface TrackTableProps {
  readonly tracks: (TrackSummary | ReleaseTrackItem | ArtistTopTrack)[]
  readonly showArtwork?: boolean
  readonly showRelease?: boolean
  readonly sourceLabel?: string
  readonly releaseContextId?: number
  readonly indexOffset?: number
}

export default function TrackTable({
  tracks,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
  releaseContextId,
  indexOffset = 0,
}: TrackTableProps) {
  if (tracks.length === 0) return null

  const showPlayCount = "play_count" in tracks[0]

  return (
    <table className="w-full table-fixed border-collapse">
      <colgroup>
        <col className="w-10" />
        {showArtwork && <col className="w-12" />}
        <col />
        {showRelease && <col className="hidden md:table-column w-[180px]" />}
        <col className={showPlayCount ? "w-44" : "w-14"} />
        <col className="w-8" />
        <col className="w-8" />
      </colgroup>
      <tbody>
        {tracks.map((track, i) => (
          <TrackRow
            key={track.id}
            track={track}
            index={indexOffset + i}
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
