import { useState } from "react"
import { useSearchParams } from "react-router"
import { useSearch } from "@/api/hooks/useSearch"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import MediaCard from "@/components/media/MediaCard"
import TrackTable from "@/components/media/TrackTable"
import { usePlayerStore } from "@/store/playerStore"
import type { ArtistSummary, ReleaseSummary, TrackSummary } from "@/api/types"
type TabKey = "all" | "artists" | "releases" | "tracks"

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

  return (
    <div className="py-4 space-y-6">
      {/* Empty / no query */}
      {!urlQuery && (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">Type something to search your library.</p>
      )}

      {/* Loading */}
      {isLoading && urlQuery && <ResultSkeleton />}

      {/* No results */}
      {!isLoading && urlQuery && !hasResults && (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">No results for "{urlQuery}".</p>
      )}

      {/* Results */}
      {hasResults && (
        <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)} className="px-4 sm:px-6">
          <TabsList>
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="artists" disabled={artists.length === 0}>
              Artists {artists.length > 0 && `(${groups["artists"]?.total ?? artists.length})`}
            </TabsTrigger>
            <TabsTrigger value="releases" disabled={releases.length === 0}>
              Releases {releases.length > 0 && `(${groups["releases"]?.total ?? releases.length})`}
            </TabsTrigger>
            <TabsTrigger value="tracks" disabled={tracks.length === 0}>
              Tracks {tracks.length > 0 && `(${groups["tracks"]?.total ?? tracks.length})`}
            </TabsTrigger>
          </TabsList>

          {/* Top result */}
          {(tab === "all") && data?.top_result && (
            <div className="mt-4 mb-6">
              <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wide">Top result</p>
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

          {/* Artists */}
          <TabsContent value="all">
            {artists.length > 0 && (
              <section className="space-y-3 mt-2">
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Artists</h2>
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
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Releases</h2>
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
                <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Tracks</h2>
                <TrackTable tracks={tracks.slice(0, 8)} sourceLabel={`Search: ${urlQuery}`} />
              </section>
            )}
          </TabsContent>

          <TabsContent value="artists">
            <div className="flex flex-wrap gap-1 mt-2">
              {artists.map((a) => (
                <MediaCard key={a.id} id={a.id} type="artist" title={a.name} artwork={a.image}
                  onPlay={() => playSource("artist", a.id, a.name)} />
              ))}
            </div>
          </TabsContent>

          <TabsContent value="releases">
            <div className="flex flex-wrap gap-1 mt-2">
              {releases.map((r) => (
                <MediaCard key={r.id} id={r.id} type="release" title={r.title}
                  subtitle={r.artists.map((a) => a.name).join(", ")}
                  artwork={r.artwork}
                  onPlay={() => playSource("release", r.id, r.title)} />
              ))}
            </div>
          </TabsContent>

          <TabsContent value="tracks">
            <div className="mt-2">
              <TrackTable tracks={tracks} sourceLabel={`Search: ${urlQuery}`} />
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
