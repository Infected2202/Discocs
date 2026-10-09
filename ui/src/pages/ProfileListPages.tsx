import { useParams, useSearchParams } from "react-router"
import { useTranslation } from "react-i18next"
import { apiFetch } from "@/api/client"
import {
  useUserAlbumsForYouList,
  useUserLikesList,
  useUserMixesList,
  useUserPlaylistsList,
  useUserTopList,
  useUserTopTracksList,
} from "@/api/hooks/useProfile"
import {
  DEFAULT_PROFILE_PERIOD,
  PROFILE_LIKE_KINDS,
  PROFILE_TOP_KINDS,
  isProfilePeriod,
  type ProfileLikeKind,
  type ProfilePeriod,
  type ProfileShelfItem,
  type ProfileTopKind,
} from "@/api/profile"
import type { PlaybackEnvelope, PlaylistSummary, ShelfItem } from "@/api/types"
import FullListPage, { FullListHeader, useInfiniteScrollSentinel } from "@/components/media/FullListPage"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import { Skeleton } from "@/components/ui/skeleton"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"
import { profilePlaylistCard, profileTopCard } from "@/components/profile/profileCards"
import { usePlayShelfItem } from "@/hooks/usePlayShelfItem"
import { usePlayerStore } from "@/store/playerStore"

// Full lists behind the profile's horizontal shelves (docs/social.md
// «Полные списки»): `/u/:username/top/:kind?period=`, `/u/:username/likes/:kind`
// and `/u/:username/playlists`, plus the personal recommendations
// `/u/:username/mixes` and `/u/:username/albums-for-you` (their titles name the
// user). The username is the subtitle. Top tracks are
// track rows with their listens, like the profile's top tracks list.

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

function usePeriodParam(): ProfilePeriod {
  const [searchParams] = useSearchParams()
  const periodParam = searchParams.get("period")
  return isProfilePeriod(periodParam) ? periodParam : DEFAULT_PROFILE_PERIOD
}

/** `/u/:username/top/:kind?period=` — a period's full top artists/releases/tracks. */
export function ProfileTopPage() {
  const { username = "", kind = "" } = useParams<{ username: string; kind: string }>()
  if (kind === "tracks") return <ProfileTopTracksList username={username} />
  const valid = (PROFILE_TOP_KINDS as readonly string[]).includes(kind)
  return valid ? <ProfileTopList username={username} kind={kind as ProfileTopKind} /> : <NotFound />
}

function ProfileTopTracksList({ username }: { readonly username: string }) {
  const { t } = useTranslation("user")
  const period = usePeriodParam()
  const source = useUserTopTracksList(username, period)
  const sentinelRef = useInfiniteScrollSentinel(source)
  // Same title as the profile's list: «Топ треков (30 дн.)».
  const title = `${t("sections.topTracks")} (${t(`periodSuffix.${period}`)})`
  // play_count is the row's built-in "N plays" metric, as on the profile.
  const tracks = source.data?.pages.flatMap((page) => page.items).map((track) => ({ ...track, play_count: track.listens })) ?? []

  return (
    <div className="py-6 space-y-6">
      <FullListHeader title={title} subtitle={username} total={source.data?.pages[0]?.total} />
      {source.isLoading ? (
        <RowSkeletons />
      ) : (
        <div className="px-4 sm:px-6">
          <VirtualTrackList tracks={tracks} showRelease sourceLabel={title} />
        </div>
      )}
      <div ref={sentinelRef} className="h-4" data-testid="full-list-sentinel" />
      {source.isFetchingNextPage && <RowSkeletons />}
    </div>
  )
}

function RowSkeletons() {
  return (
    <div className="px-4 sm:px-6 space-y-3">
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <Skeleton key={i} className="h-12 w-full rounded-md" />
      ))}
    </div>
  )
}

function ProfileTopList({ username, kind }: { readonly username: string; readonly kind: ProfileTopKind }) {
  const { t, i18n } = useTranslation("user")
  const period = usePeriodParam()
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

/** `/u/:username/mixes` — all the user's generated mixes. */
export function ProfileMixesPage() {
  const { t, i18n } = useTranslation("user")
  const { username = "" } = useParams<{ username: string }>()
  const playShelfItem = usePlayShelfItem()
  const source = useUserMixesList(username)

  return (
    <FullListPage<ShelfItem>
      title={t("sections.mixesFor", { username })}
      source={source}
      getKey={shelfItemKey}
      toCard={(item) => shelfItemToCard(item, playShelfItem, t, i18n.language)}
    />
  )
}

/** `/u/:username/albums-for-you` — all the user's recommended albums. */
export function ProfileAlbumsForYouPage() {
  const { t, i18n } = useTranslation("user")
  const { username = "" } = useParams<{ username: string }>()
  const playShelfItem = usePlayShelfItem()
  const source = useUserAlbumsForYouList(username)

  return (
    <FullListPage<ShelfItem>
      title={t("sections.albumsFor", { username })}
      source={source}
      getKey={shelfItemKey}
      toCard={(item) => shelfItemToCard(item, playShelfItem, t, i18n.language)}
    />
  )
}
