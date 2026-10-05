import { useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { useArtist, useArtistLabelsList } from "@/api/hooks/useArtist"
import type { LabelSummary } from "@/api/types"
import FullListPage from "@/components/media/FullListPage"
import { labelToCard } from "@/components/media/labelCard"

/** `/artists/:id/labels` — every label of the artist's releases, behind the shelf's «Ещё». */
export default function ArtistLabelsPage() {
  const { t } = useTranslation("artist")
  const { id } = useParams<{ id: string }>()
  const artistId = Number(id)
  const { data: artistData } = useArtist(artistId)
  const source = useArtistLabelsList(artistId)

  return (
    <FullListPage<LabelSummary>
      title={t("labels")}
      subtitle={artistData?.artist.name}
      source={source}
      getKey={(label) => `label-${label.id}`}
      toCard={(label) => labelToCard(label, t)}
    />
  )
}
