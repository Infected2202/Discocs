import { useState } from "react"
import { useParams, useSearchParams } from "react-router"
import { useTranslation } from "react-i18next"
import { Pencil } from "lucide-react"
import {
  useUserLikes,
  useUserPlaylists,
  useRefreshListensOnPlayChange,
  useUserProfile,
} from "@/api/hooks/useProfile"
import { usePeople } from "@/api/hooks/usePeople"
import { apiFetch } from "@/api/client"
import {
  DEFAULT_PROFILE_PERIOD,
  PROFILE_PERIODS,
  isProfilePeriod,
  listenAlong,
  type ProfilePeriod,
  type ProfileShelfItem,
  type UserProfile,
} from "@/api/profile"
import type { PlaybackEnvelope, PlaylistSummary, ShelfItem } from "@/api/types"
import { isNetworkError } from "@/lib/apiErrorKind"
import { avatarUrl } from "@/lib/avatars"
import { withoutCurrentPlay } from "@/lib/listenHistory"
import { cn } from "@/lib/utils"
import { usePlayerStore } from "@/store/playerStore"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import Shelf from "@/components/media/Shelf"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"
import AvatarPickerDialog from "@/components/profile/AvatarPickerDialog"
import { listensLabel, profilePlaylistCard, profileTopCard } from "@/components/profile/profileCards"
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
  // The period lives in the URL (?period=), so coming back from a top's full
  // list (which carries the same period) restores the same view.
  const [searchParams, setSearchParams] = useSearchParams()
  const periodParam = searchParams.get("period")
  const period: ProfilePeriod = isProfilePeriod(periodParam) ? periodParam : DEFAULT_PROFILE_PERIOD
  const setPeriod = (next: ProfilePeriod) => {
    setSearchParams(
      (params) => {
        const updated = new URLSearchParams(params)
        if (next === DEFAULT_PROFILE_PERIOD) updated.delete("period")
        else updated.set("period", next)
        return updated
      },
      { replace: true },
    )
  }
  const [pickerOpen, setPickerOpen] = useState(false)
  const [joining, setJoining] = useState(false)
  const { data: profile, isLoading, isPlaceholderData, error } = useUserProfile(username, period)
  const { data: likes } = useUserLikes(username)
  const { data: playlists } = useUserPlaylists(username)
  const { data: people } = usePeople()
  // The people list includes the viewer, so this covers one's own profile too.
  const nowPlaying = people
    ? (people.items.find((p) => p.username.toLowerCase() === username.toLowerCase())?.now_playing ?? null)
    : undefined
  useRefreshListensOnPlayChange(username, nowPlaying)
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

  // One-shot "pick up" of this user's playback: the rest of their queue from
  // their current position, in a session of our own (docs/social.md).
  async function listenAlongWithUser() {
    setJoining(true)
    try {
      const envelope = await listenAlong(header.username)
      await playFromEnvelope(envelope, envelope.start_track_id, {
        startPositionSeconds: envelope.start_position_seconds,
      })
    } catch {
      // they stopped meanwhile — the people poll hides the status shortly
    } finally {
      setJoining(false)
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
  const topCard = (item: ProfileShelfItem) => profileTopCard(item, playShelfItem, t, locale)
  const playlistCard = (playlist: PlaylistSummary) =>
    profilePlaylistCard(playlist, (endpoint) => void playEnvelope(endpoint), t)

  const { header } = profile
  const isOwner = header.viewer_is_owner
  const createdAt = new Date(header.created_at)
  const memberSince = Number.isNaN(createdAt.getTime())
    ? null
    : new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric" }).format(createdAt)
  const hasListens = header.totals.listens > 0
  const recent = withoutCurrentPlay(profile.recent, nowPlaying?.track)
  // Period-driven sections say which period they show: «Статистика (30 дн.)».
  const forPeriod = (title: string) => `${title} (${t(`periodSuffix.${period}`)})`
  const profilePath = `/u/${encodeURIComponent(header.username)}`
  const historyHref = `${profilePath}/history`
  // Full lists of the shelves (docs/web-ui.md «Полки»); tops keep the period.
  const topHref = (kind: "artists" | "releases") => `${profilePath}/top/${kind}?period=${period}`

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
        { key: "tracks", title: t("sections.likedTracks"), ...likes.tracks },
        { key: "releases", title: t("sections.likedReleases"), ...likes.releases },
        { key: "artists", title: t("sections.likedArtists"), ...likes.artists },
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
        align="center"
        meta={
          <>
            <p>
              {memberSince && `${t("memberSince", { date: memberSince })} · `}
              {listensLabel(t, locale, header.totals.listens)}
              {` · ${t("artistCount", { count: header.totals.artists, formatted: number.format(header.totals.artists) })}`}
              {` · ${t("likeCount", { count: header.totals.likes, formatted: number.format(header.totals.likes) })}`}
            </p>
            {nowPlaying && (
              <p className="mt-2 flex items-center gap-2 text-foreground" data-testid="now-playing">
                <LiveDot />
                <span className="min-w-0 truncate">
                  {t("nowPlaying")}: {nowPlaying.artists ? `${nowPlaying.artists} - ` : ""}
                  {nowPlaying.title}
                </span>
                {!isOwner && nowPlaying.track && (
                  <button
                    type="button"
                    onClick={() => void listenAlongWithUser()}
                    disabled={joining}
                    data-testid="listen-along"
                    className="shrink-0 rounded-full border border-green-500/60 px-3 py-0.5 text-sm font-medium text-green-500 transition-colors hover:bg-green-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                  >
                    {t("listenAlong")}
                  </button>
                )}
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

          {(recent.length > 0 || nowPlaying?.track) && (
            <ProfileSection title={t("sections.recent")} moreHref={historyHref} moreLabel={t("seeAll")}>
              <div className="px-4 sm:px-6">
                <ListenRows listens={recent} nowPlaying={nowPlaying?.track} sourceLabel={t("sections.recent")} />
              </div>
            </ProfileSection>
          )}

          <ProfileSection title={forPeriod(t("sections.stats"))}>
            {profile.summary.listens > 0 ? (
              <ProfileStats profile={profile} />
            ) : (
              <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("empty.noListensInPeriod")}</p>
            )}
          </ProfileSection>

          <Shelf
            title={forPeriod(t("sections.topArtists"))}
            items={profile.top_artists.map(topCard)}
            total={profile.top_artists_total}
            moreHref={topHref("artists")}
          />
          <Shelf
            title={forPeriod(t("sections.topReleases"))}
            items={profile.top_releases.map(topCard)}
            total={profile.top_releases_total}
            moreHref={topHref("releases")}
          />
          <TopTracks profile={profile} title={forPeriod(t("sections.topTracks"))} />
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
              total={shelf.total}
              moreHref={`${profilePath}/likes/${shelf.key}`}
            />
          ))}
        </ProfileSection>
      )}

      {(playlists?.items.length ?? 0) > 0 && (
        <Shelf
          title={t("sections.playlists")}
          items={(playlists?.items ?? []).map(playlistCard)}
          total={playlists?.total}
          moreHref={`${profilePath}/playlists`}
        />
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
