import { useParams, useSearchParams } from "react-router"
import { useTranslation } from "react-i18next"
import { apiFetch } from "@/api/client"
import { useUserLikesList, useUserPlaylistsList, useUserTopList } from "@/api/hooks/useProfile"
import {
  DEFAULT_PROFILE_PERIOD,
  PROFILE_LIKE_KINDS,
  PROFILE_TOP_KINDS,
  isProfilePeriod,
  type ProfileLikeKind,
  type ProfileShelfItem,
  type ProfileTopKind,
} from "@/api/profile"
import type { PlaybackEnvelope, PlaylistSummary, ShelfItem } from "@/api/types"
import FullListPage from "@/components/media/FullListPage"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"
import { profilePlaylistCard, profileTopCard } from "@/components/profile/profileCards"
import { usePlayShelfItem } from "@/hooks/usePlayShelfItem"
import { usePlayerStore } from "@/store/playerStore"

// Full lists behind the profile's horizontal shelves (docs/social.md
// «Полные списки»): `/u/:username/top/:kind?period=`, `/u/:username/likes/:kind`
// and `/u/:username/playlists`. The username is the subtitle.

const TOP_TITLES: Record<ProfileTopKind, string> = {
  artists: "sections.topArtists",
  releases: "sections.topReleases",
}

const LIKE_TITLES: Record<ProfileLikeKind, string> = {
  tracks: "lists.likedTracks",
  releases: "lists.likedReleases",
  artists: "lists.likedArtists",
}

const shelfItemKey = (item: ShelfItem) => `${item.entity_type}-${item.entity_id}`

function NotFound() {
  const { t } = useTranslation("user")
  return (
    <div className="p-8">
      <p className="text-destructive text-sm">{t("lists.notFound")}</p>
    </div>
  )
}

/** `/u/:username/top/:kind?period=` — a period's full top artists/releases. */
export function ProfileTopPage() {
  const { username = "", kind = "" } = useParams<{ username: string; kind: string }>()
  const valid = (PROFILE_TOP_KINDS as readonly string[]).includes(kind)
  return valid ? <ProfileTopList username={username} kind={kind as ProfileTopKind} /> : <NotFound />
}

function ProfileTopList({ username, kind }: { readonly username: string; readonly kind: ProfileTopKind }) {
  const { t, i18n } = useTranslation("user")
  const [searchParams] = useSearchParams()
  const periodParam = searchParams.get("period")
  const period = isProfilePeriod(periodParam) ? periodParam : DEFAULT_PROFILE_PERIOD
  const playShelfItem = usePlayShelfItem()
  const source = useUserTopList(username, kind, period)

  return (
    <FullListPage<ProfileShelfItem>
      // Same title as the shelf: «Топ артистов (30 дн.)».
      title={`${t(TOP_TITLES[kind])} (${t(`periodSuffix.${period}`)})`}
      subtitle={username}
      source={source}
      getKey={shelfItemKey}
      toCard={(item) => profileTopCard(item, playShelfItem, t, i18n.language)}
    />
  )
}

/** `/u/:username/likes/:kind` — all liked tracks, releases or artists. */
export function ProfileLikesPage() {
  const { username = "", kind = "" } = useParams<{ username: string; kind: string }>()
  const valid = (PROFILE_LIKE_KINDS as readonly string[]).includes(kind)
  return valid ? <ProfileLikesList username={username} kind={kind as ProfileLikeKind} /> : <NotFound />
}

function ProfileLikesList({ username, kind }: { readonly username: string; readonly kind: ProfileLikeKind }) {
  const { t, i18n } = useTranslation("user")
  const playShelfItem = usePlayShelfItem()
  const source = useUserLikesList(username, kind)

  return (
    <FullListPage<ShelfItem>
      title={t(LIKE_TITLES[kind])}
      subtitle={username}
      source={source}
      getKey={shelfItemKey}
      toCard={(item) => shelfItemToCard(item, playShelfItem, t, i18n.language)}
    />
  )
}

/** `/u/:username/playlists` — all playlists the viewer may see. */
export function ProfilePlaylistsPage() {
  const { t } = useTranslation("user")
  const { username = "" } = useParams<{ username: string }>()
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const source = useUserPlaylistsList(username)

  function playEndpoint(endpoint: string) {
    apiFetch<PlaybackEnvelope>(endpoint, { method: "POST" })
      .then((envelope) => playFromEnvelope(envelope))
      .catch(() => {})
  }

  return (
    <FullListPage<PlaylistSummary>
      title={t("sections.playlists")}
      subtitle={username}
      source={source}
      getKey={(playlist) => `playlist-${playlist.id}`}
      toCard={(playlist) => profilePlaylistCard(playlist, playEndpoint, t)}
    />
  )
}
