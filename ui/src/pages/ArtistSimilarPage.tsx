import { useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { useArtist, useArtistSimilarList } from "@/api/hooks/useArtist"
import type { ArtistSummary } from "@/api/types"
import FullListPage from "@/components/media/FullListPage"
import type { MediaCardProps } from "@/components/media/MediaCard"
import { usePlayerStore } from "@/store/playerStore"

/** A similar-artist card, the same as on the artist page's shelf. */
function similarArtistCard(artist: ArtistSummary, onPlay: () => void): MediaCardProps {
  return { id: artist.id, type: "artist", title: artist.name, artwork: artist.image, onPlay }
}

/** `/artists/:id/similar` — the full similar-artists list of an artist. */
export default function ArtistSimilarPage() {
  const { t } = useTranslation("artist")
  const { id } = useParams<{ id: string }>()
  const artistId = Number(id)
  const { data: artistData } = useArtist(artistId)
  const playSource = usePlayerStore((s) => s.playSource)
  const source = useArtistSimilarList(artistId)

  return (
    <FullListPage<ArtistSummary>
      title={t("similarArtists")}
      subtitle={artistData?.artist.name}
      source={source}
      getKey={(artist) => `artist-${artist.id}`}
      toCard={(artist) => similarArtistCard(artist, () => playSource("artist", artist.id, artist.name))}
    />
  )
}
