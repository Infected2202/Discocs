import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { useTranslation } from "react-i18next"
import { useSearch, useInfiniteSearch } from "@/api/hooks/useSearch"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import MediaCard from "@/components/media/MediaCard"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import { usePlayerStore } from "@/store/playerStore"
import type { ArtistSummary, ReleaseSummary, TrackSummary } from "@/api/types"
type TabKey = "all" | "artists" | "releases" | "tracks"

/** Infinite-scroll sentinel: fires fetchNextPage() when it enters the viewport. */
function useLoadMoreSentinel(hasNextPage: boolean | undefined, isFetchingNextPage: boolean, fetchNextPage: () => void) {
  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) fetchNextPage()
      },
      { rootMargin: "200px" },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])
  return sentinelRef
}

function ResultSkeleton() {
  return (
    <div className="space-y-6 px-4 sm:px-6 py-4">
      <Skeleton className="h-5 w-48" />
      <div className="flex gap-2">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="w-44 p-3 space-y-3">
            <Skeleton className="w-full aspect-square rounded-md" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ))}
      </div>
    </div>
  )
}

export default function SearchPage() {
  const { t } = useTranslation("search")
  const [searchParams] = useSearchParams()
  const urlQuery = searchParams.get("q") ?? ""
  const [tab, setTab] = useState<TabKey>("all")
  const playSource = usePlayerStore((s) => s.playSource)

  const { data, isLoading } = useSearch(urlQuery, "all", 12)

  const groups = Object.fromEntries((data?.groups ?? []).map((g) => [g.type, g]))
  const artists = (groups["artists"]?.items ?? []) as ArtistSummary[]
  const releases = (groups["releases"]?.items ?? []) as ReleaseSummary[]
  const tracks = (groups["tracks"]?.items ?? []) as TrackSummary[]

  const hasResults = artists.length > 0 || releases.length > 0 || tracks.length > 0

  // Each specific tab paginates through ALL of its matches independently —
  // the "all" query above stays a small fixed-size preview.
  const artistsQuery = useInfiniteSearch(urlQuery, "artist", tab === "artists")
  const releasesQuery = useInfiniteSearch(urlQuery, "release", tab === "releases")
  const tracksQuery = useInfiniteSearch(urlQuery, "track", tab === "tracks")

  const allArtists = artistsQuery.data?.pages.flatMap((p) => p.groups.find((g) => g.type === "artists")?.items ?? []) as ArtistSummary[] | undefined
  const allReleases = releasesQuery.data?.pages.flatMap((p) => p.groups.find((g) => g.type === "releases")?.items ?? []) as ReleaseSummary[] | undefined
  const allTracks = tracksQuery.data?.pages.flatMap((p) => p.groups.find((g) => g.type === "tracks")?.items ?? []) as TrackSummary[] | undefined

  const artistsSentinelRef = useLoadMoreSentinel(artistsQuery.hasNextPage, artistsQuery.isFetchingNextPage, artistsQuery.fetchNextPage)
  const releasesSentinelRef = useLoadMoreSentinel(releasesQuery.hasNextPage, releasesQuery.isFetchingNextPage, releasesQuery.fetchNextPage)
  const tracksSentinelRef = useLoadMoreSentinel(tracksQuery.hasNextPage, tracksQuery.isFetchingNextPage, tracksQuery.fetchNextPage)

  return (
    <div className="py-4 space-y-6">
      {/* Empty / no query */}
      {!urlQuery && (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("typeToSearch")}</p>
      )}

      {/* Loading */}
      {isLoading && urlQuery && <ResultSkeleton />}

      {/* No results */}
      {!isLoading && urlQuery && !hasResults && (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("noResults", { query: urlQuery })}</p>
      )}

      {/* Results */}
      {hasResults && (
        <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)} className="px-4 sm:px-6">
          <TabsList>
            <TabsTrigger value="all">{t("tabs.all")}</TabsTrigger>
            <TabsTrigger value="artists" disabled={artists.length === 0}>
              {t("tabs.artists")} {artists.length > 0 && `(${groups["artists"]?.total ?? artists.length})`}
            </TabsTrigger>
            <TabsTrigger value="releases" disabled={releases.length === 0}>
              {t("tabs.releases")} {releases.length > 0 && `(${groups["releases"]?.total ?? releases.length})`}
            </TabsTrigger>
            <TabsTrigger value="tracks" disabled={tracks.length === 0}>
              {t("tabs.tracks")} {tracks.length > 0 && `(${groups["tracks"]?.total ?? tracks.length})`}
            </TabsTrigger>
          </TabsList>

          {/* Top result */}
          {(tab === "all") && data?.top_result && (
            <div className="mt-4 mb-6">
              <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wide">{t("topResult")}</p>
              <div className="inline-block">
                <MediaCard
                  id={"id" in data.top_result.entity ? (data.top_result.entity as { id: number }).id : 0}
                  type={data.top_result.entity_type as "artist" | "release" | "generated_mix"}
                  title={"name" in data.top_result.entity
                    ? (data.top_result.entity as ArtistSummary).name
                    : (data.top_result.entity as ReleaseSummary).title}
                  subtitle={null}
                  artwork={
                    "image" in data.top_result.entity
                      ? (data.top_result.entity as ArtistSummary).image
                      : (data.top_result.entity as ReleaseSummary).artwork
                  }
                  onPlay={() => {
                    const e = data.top_result!.entity as ArtistSummary & ReleaseSummary & TrackSummary
                    const id = e.id
                    const label = e.name ?? e.title
                    playSource(data.top_result!.entity_type, id, label)
                  }}
                />
              </div>
            </div>
          )}

          {/* Overview: a small preview per group, with a button to the full tab */}
          <TabsContent value="all">
            {artists.length > 0 && (
              <section className="space-y-3 mt-2">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">{t("tabs.artists")}</h2>
                  {(groups["artists"]?.total ?? artists.length) > 6 && (
                    <button
                      onClick={() => setTab("artists")}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {t("showAll")}
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1">
                  {artists.slice(0, 6).map((a) => (
                    <MediaCard key={a.id} id={a.id} type="artist" title={a.name} artwork={a.image}
                      onPlay={() => playSource("artist", a.id, a.name)} />
                  ))}
                </div>
              </section>
            )}
            {releases.length > 0 && (
              <section className="space-y-3 mt-6">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">{t("tabs.releases")}</h2>
                  {(groups["releases"]?.total ?? releases.length) > 6 && (
                    <button
                      onClick={() => setTab("releases")}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {t("showAll")}
                    </button>
                  )}
                </div>
                <div className="flex flex-wrap gap-1">
                  {releases.slice(0, 6).map((r) => (
                    <MediaCard key={r.id} id={r.id} type="release" title={r.title}
                      subtitle={r.artists.map((a) => a.name).join(", ")}
                      artwork={r.artwork}
                      onPlay={() => playSource("release", r.id, r.title)} />
                  ))}
                </div>
              </section>
            )}
            {tracks.length > 0 && (
              <section className="space-y-3 mt-6">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">{t("tabs.tracks")}</h2>
                  {(groups["tracks"]?.total ?? tracks.length) > 8 && (
                    <button
                      onClick={() => setTab("tracks")}
                      className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {t("showAll")}
                    </button>
                  )}
                </div>
                <VirtualTrackList tracks={tracks.slice(0, 8)} virtualized={false} sourceLabel={t("sourceLabel", { query: urlQuery })} />
              </section>
            )}
          </TabsContent>

          <TabsContent value="artists">
            {artistsQuery.isLoading ? (
              <div className="flex flex-wrap gap-1 mt-2">
                {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="w-44 h-16" />)}
              </div>
            ) : (
              <>
                <div className="flex flex-wrap gap-1 mt-2">
                  {(allArtists ?? []).map((a) => (
                    <MediaCard key={a.id} id={a.id} type="artist" title={a.name} artwork={a.image}
                      onPlay={() => playSource("artist", a.id, a.name)} />
                  ))}
                </div>
                <div ref={artistsSentinelRef} className="h-4" />
                {artistsQuery.isFetchingNextPage && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="w-44 h-16" />)}
                  </div>
                )}
              </>
            )}
          </TabsContent>

          <TabsContent value="releases">
            {releasesQuery.isLoading ? (
              <div className="flex flex-wrap gap-1 mt-2">
                {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="w-44 h-56" />)}
              </div>
            ) : (
              <>
                <div className="flex flex-wrap gap-1 mt-2">
                  {(allReleases ?? []).map((r) => (
                    <MediaCard key={r.id} id={r.id} type="release" title={r.title}
                      subtitle={r.artists.map((a) => a.name).join(", ")}
                      artwork={r.artwork}
                      onPlay={() => playSource("release", r.id, r.title)} />
                  ))}
                </div>
                <div ref={releasesSentinelRef} className="h-4" />
                {releasesQuery.isFetchingNextPage && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="w-44 h-56" />)}
                  </div>
                )}
              </>
            )}
          </TabsContent>

          <TabsContent value="tracks">
            {tracksQuery.isLoading ? (
              <div className="space-y-1 mt-2">
                {[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-12 w-full rounded-md" />)}
              </div>
            ) : (
              <>
                <div className="mt-2">
                  <VirtualTrackList tracks={allTracks ?? []} sourceLabel={t("sourceLabel", { query: urlQuery })} />
                </div>
                <div ref={tracksSentinelRef} className="h-4" />
              </>
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
