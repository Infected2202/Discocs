import { useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { useRelease, useReleaseRecommendationsList, useReleaseRelatedList } from "@/api/hooks/useRelease"
import type { ReleaseSummary, ShelfItem } from "@/api/types"
import FullListPage from "@/components/media/FullListPage"
import { usePlayerStore } from "@/store/playerStore"

// Full lists behind the release page's shelves: «Ещё от этих артистов»
// (`/releases/:id/related`) and «Рекомендованные альбомы»
// (`/releases/:id/recommendations`). The release title is the subtitle.

function useReleaseId(): number {
  const { id } = useParams<{ id: string }>()
  return Number(id)
}

/** `/releases/:id/related` — every other release of the release's artists. */
export function ReleaseRelatedPage() {
  const { t } = useTranslation("release")
  const releaseId = useReleaseId()
  const { data: releaseData } = useRelease(releaseId)
  const playSource = usePlayerStore((s) => s.playSource)
  const source = useReleaseRelatedList(releaseId)

  return (
    <FullListPage<ReleaseSummary>
      title={t("moreFromArtists")}
      subtitle={releaseData?.release.title}
      source={source}
      getKey={(release) => `release-${release.id}`}
      toCard={(release) => ({
        id: release.id,
        type: "release",
        title: release.title,
        subtitle: release.artists.map((artist) => artist.name).join(", "),
        artwork: release.artwork,
        onPlay: () => playSource("release", release.id, release.title),
      })}
    />
  )
}

/** `/releases/:id/recommendations` — the full recommended-albums list. */
export function ReleaseRecommendationsPage() {
  const { t } = useTranslation("release")
  const releaseId = useReleaseId()
  const { data: releaseData } = useRelease(releaseId)
  const playSource = usePlayerStore((s) => s.playSource)
  const source = useReleaseRecommendationsList(releaseId)

  return (
    <FullListPage<ShelfItem>
      title={t("recommendedAlbums")}
      subtitle={releaseData?.release.title}
      source={source}
      getKey={(item) => `${item.entity_type}-${item.entity_id}`}
      toCard={(item) => ({
        id: item.entity_id,
        type: "release",
        title: item.title,
        subtitle: item.subtitle,
        subtitleLinks: item.subtitle_links,
        href: item.action?.target,
        reason: item.reason,
        artwork: item.artwork,
        onPlay: item.play_action
          ? () => playSource("release", Number(item.entity_id), item.title)
          : undefined,
      })}
    />
  )
}
