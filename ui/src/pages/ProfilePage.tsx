import { useState } from "react"
import { useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { Pencil } from "lucide-react"
import {
  useUserLikes,
  useUserPlaylists,
  useUserProfile,
} from "@/api/hooks/useProfile"
import { usePeople } from "@/api/hooks/usePeople"
import { apiFetch } from "@/api/client"
import {
  DEFAULT_PROFILE_PERIOD,
  PROFILE_PERIODS,
  type ProfilePeriod,
  type ProfileShelfItem,
  type UserProfile,
} from "@/api/profile"
import type { PlaybackEnvelope, PlaylistSummary, ShelfItem } from "@/api/types"
import { isNetworkError } from "@/lib/apiErrorKind"
import { avatarUrl } from "@/lib/avatars"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import Shelf from "@/components/media/Shelf"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import type { MediaCardProps } from "@/components/media/MediaCard"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"
import AvatarPickerDialog from "@/components/profile/AvatarPickerDialog"
import ListenRows from "@/components/profile/ListenRows"
import ProfileSection from "@/components/profile/ProfileSection"
import ProfileStats from "@/components/profile/ProfileStats"

const AVATAR_SIZE = 144

function ProfilePageSkeleton() {
  return (
    <div className="space-y-8" data-testid="profile-skeleton">
      <div className="px-4 sm:px-6 pt-8 flex flex-col sm:flex-row gap-6 sm:items-end">
        <Skeleton className="w-36 h-36 rounded-full shrink-0" />
        <div className="space-y-3 pb-2">
          <Skeleton className="h-9 w-48" />
          <Skeleton className="h-4 w-72 max-w-full" />
        </div>
      </div>
      <div className="px-4 sm:px-6">
        <Skeleton className="h-8 w-80 max-w-full" />
      </div>
      <div className="px-4 sm:px-6 space-y-3">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-md" />
        ))}
      </div>
    </div>
  )
}

/** Pulsing green dot of a user who is playing right now. */
function LiveDot() {
  return (
    <span
      aria-hidden="true"
      data-testid="live-dot"
      className="inline-block h-2 w-2 shrink-0 rounded-full bg-green-500 motion-safe:animate-pulse"
    />
  )
}

export default function ProfilePage() {
  const { t, i18n } = useTranslation("user")
  const { username = "" } = useParams<{ username: string }>()
  const [period, setPeriod] = useState<ProfilePeriod>(DEFAULT_PROFILE_PERIOD)
  const [pickerOpen, setPickerOpen] = useState(false)
  const { data: profile, isLoading, isPlaceholderData, error } = useUserProfile(username, period)
  const { data: likes } = useUserLikes(username)
  const { data: playlists } = useUserPlaylists(username)
  const { data: people } = usePeople()
  const playSource = usePlayerStore((s) => s.playSource)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)

  if (isLoading) return <ProfilePageSkeleton />
  if (error || !profile) {
    if (error && isNetworkError(error)) {
      return (
        <div className="p-8">
          <p className="text-muted-foreground text-sm">{t("status.reconnecting", { ns: "common" })}</p>
        </div>
      )
    }
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">{t("notFound")}</p>
      </div>
    )
  }

  async function playEnvelope(endpoint: string) {
    try {
      const envelope = await apiFetch<PlaybackEnvelope>(endpoint, { method: "POST" })
      await playFromEnvelope(envelope)
    } catch {
      // silently ignore, like the dashboard shelves
    }
  }

  function playShelfItem(item: ShelfItem) {
    const action = item.play_action
    if (!action) return
    if (action.type === "post") void playEnvelope(action.endpoint)
    else playSource(action.source_type, Number(action.source_id), action.source_label ?? item.title)
  }

  const locale = i18n.language
  const number = new Intl.NumberFormat(locale)
  const listensLabel = (count: number) => t("listenCount", { count, formatted: number.format(count) })

  function topCard(item: ProfileShelfItem): MediaCardProps {
    const count = listensLabel(item.listens)
    return {
      ...shelfItemToCard(item, playShelfItem, t),
      // The listens count replaces the artist links: one plain subtitle line.
      subtitleLinks: undefined,
      subtitle: item.subtitle ? `${item.subtitle} · ${count}` : count,
    }
  }

  function playlistCard(playlist: PlaylistSummary): MediaCardProps {
    return {
      id: playlist.id,
      type: "playlist",
      title: playlist.title,
      subtitle: t("trackCount", { ns: "playlist", count: playlist.track_count }),
      artwork: playlist.artwork,
      href: playlist.action.target,
      onPlay: () => void playEnvelope(playlist.play_action.endpoint),
    }
  }

  const { header } = profile
  const isOwner = header.viewer_is_owner
  const nowPlaying = isOwner
    ? null
    : people?.items.find((p) => p.username.toLowerCase() === header.username.toLowerCase())?.now_playing ?? null
  const createdAt = new Date(header.created_at)
  const memberSince = Number.isNaN(createdAt.getTime())
    ? null
    : new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric" }).format(createdAt)
  const hasListens = header.totals.listens > 0
  const historyHref = `/u/${encodeURIComponent(header.username)}/history`

  const avatar = (
    <ArtworkImage
      src={avatarUrl(header.avatar)}
      alt={header.username}
      size={AVATAR_SIZE}
      className="rounded-full shrink-0"
      fallbackLetter={header.username[0]?.toUpperCase()}
    />
  )

  const likeShelves = likes
    ? [
        { key: "tracks", title: t("sections.likedTracks"), items: likes.tracks.items },
        { key: "releases", title: t("sections.likedReleases"), items: likes.releases.items },
        { key: "artists", title: t("sections.likedArtists"), items: likes.artists.items },
      ].filter((shelf) => shelf.items.length > 0)
    : []

  return (
    <div className="relative pb-8 space-y-6">
      <CollectionHeader
        artwork={
          isOwner ? (
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              aria-label={t("chooseAvatar")}
              title={t("chooseAvatar")}
              className="group relative block shrink-0 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {avatar}
              <span
                aria-hidden="true"
                className="absolute inset-0 flex items-center justify-center rounded-full bg-black/40 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              >
                <Pencil size={24} />
              </span>
            </button>
          ) : (
            avatar
          )
        }
        title={header.username}
        meta={
          <>
            <p>
              {memberSince && `${t("memberSince", { date: memberSince })} · `}
              {listensLabel(header.totals.listens)}
              {` · ${t("artistCount", { count: header.totals.artists, formatted: number.format(header.totals.artists) })}`}
              {` · ${t("likeCount", { count: header.totals.likes, formatted: number.format(header.totals.likes) })}`}
            </p>
            {nowPlaying && (
              <p className="mt-2 flex items-center gap-2 text-foreground" data-testid="now-playing">
                <LiveDot />
                <span className="min-w-0 truncate">
                  {t("nowPlaying")}: {nowPlaying.title}
                  {nowPlaying.artists ? ` — ${nowPlaying.artists}` : ""}
                </span>
              </p>
            )}
          </>
        }
      />

      {isOwner && (
        <AvatarPickerDialog open={pickerOpen} onOpenChange={setPickerOpen} current={header.avatar} />
      )}

      {hasListens ? (
        <div className={cn("space-y-6 transition-opacity", isPlaceholderData && "opacity-60")}>
          {/* Period switch: drives the stats and the tops */}
          <div className="px-4 sm:px-6 overflow-x-auto no-scrollbar">
            <Tabs value={period} onValueChange={(value) => setPeriod(value as ProfilePeriod)}>
              <TabsList aria-label={t("periodLabel")}>
                {PROFILE_PERIODS.map((key) => (
                  <TabsTrigger key={key} value={key} className="px-3">
                    {t(`periods.${key}`)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>

          {profile.recent.length > 0 && (
            <ProfileSection title={t("sections.recent")} moreHref={historyHref} moreLabel={t("seeAll")}>
              <div className="px-4 sm:px-6">
                <ListenRows listens={profile.recent} sourceLabel={t("sections.recent")} />
              </div>
            </ProfileSection>
          )}

          <ProfileSection title={t("sections.stats")}>
            {profile.summary.listens > 0 ? (
              <ProfileStats profile={profile} />
            ) : (
              <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("empty.noListensInPeriod")}</p>
            )}
          </ProfileSection>

          <Shelf title={t("sections.topArtists")} items={profile.top_artists.map(topCard)} />
          <Shelf title={t("sections.topReleases")} items={profile.top_releases.map(topCard)} />
          <TopTracks profile={profile} title={t("sections.topTracks")} />
        </div>
      ) : (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("empty.noListens")}</p>
      )}

      {likeShelves.length > 0 && (
        <ProfileSection title={t("sections.likes")}>
          {likeShelves.map((shelf) => (
            <Shelf
              key={shelf.key}
              title={shelf.title}
              items={shelf.items.map((item) => shelfItemToCard(item, playShelfItem, t))}
            />
          ))}
        </ProfileSection>
      )}

      {(playlists?.items.length ?? 0) > 0 && (
        <Shelf title={t("sections.playlists")} items={(playlists?.items ?? []).map(playlistCard)} />
      )}
    </div>
  )
}

function TopTracks({ profile, title }: { readonly profile: UserProfile; readonly title: string }) {
  if (profile.top_tracks.length === 0) return null
  // play_count is the row's built-in "N plays" metric (artist top tracks).
  const tracks = profile.top_tracks.map((track) => ({ ...track, play_count: track.listens }))
  return (
    <ProfileSection title={title}>
      <div className="px-4 sm:px-6">
        <VirtualTrackList tracks={tracks} virtualized={false} showRelease sourceLabel={title} />
      </div>
    </ProfileSection>
  )
}
