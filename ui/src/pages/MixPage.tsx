import { useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { Download, Play, Bookmark } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { useMix } from "@/api/hooks/useMix"
import { playMix, saveMix } from "@/api/mixes"
import { useUIStore } from "@/store/uiStore"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import VirtualTrackList from "@/components/media/VirtualTrackList"
import { usePlayerStore } from "@/store/playerStore"
import type { TrackSummary } from "@/api/types"

function MixPageSkeleton() {
  return (
    <div className="space-y-8">
      <div className="px-4 sm:px-6 pt-8 flex gap-6 items-end">
        <Skeleton className="w-44 h-44 rounded-lg shrink-0" />
        <div className="space-y-3 pb-2">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-8 w-56" />
          <div className="flex gap-2 mt-2">
            <Skeleton className="h-9 w-20" />
            <Skeleton className="h-9 w-20" />
          </div>
        </div>
      </div>
      <div className="px-4 sm:px-6 space-y-2">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12 w-full rounded-md" />
        ))}
      </div>
    </div>
  )
}

export default function MixPage() {
  const { t, i18n } = useTranslation("mix")
  const { id } = useParams<{ id: string }>()
  const mixId = id!
  const queryClient = useQueryClient()
  const { data: mix, isLoading, error } = useMix(mixId)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)
  const openCreatePlaylist = useUIStore((s) => s.openCreatePlaylist)

  // Starts the whole mix. With a trackId, playback begins at that track (row
  // click); without one, at the top (the header Play button).
  function playMixFrom(trackId?: number) {
    playMix(mixId).then((envelope) => playFromEnvelope(envelope, trackId)).catch(() => {})
  }

  function handleSave() {
    if (!mix) return
    openCreatePlaylist({
      defaultTitle: mix.title,
      onSubmit: async (values) => {
        await saveMix(mixId, {
          title: values.title,
          description: values.description || null,
        })
        queryClient.invalidateQueries({ queryKey: ["mix", mixId] })
        queryClient.invalidateQueries({ queryKey: ["playlists"] })
      },
    })
  }

  if (isLoading) return <MixPageSkeleton />
  if (error || !mix) {
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">{t("notFound")}</p>
      </div>
    )
  }

  const tracks = (mix.items ?? [])
    .map((item) => item.track)
    .filter(Boolean) as TrackSummary[]

  const isSaved = mix.status === "saved"

  return (
    <div className="space-y-8 pb-8">
      {/* Header */}
      <CollectionHeader
        artwork={
          <ArtworkImage
            src={mix.artwork?.url}
            alt={mix.title}
            size={176}
            className="rounded-lg shrink-0"
            fallbackLetter="M"
            expandable
          />
        }
        kicker={t("generatedMix")}
        title={mix.title}
        meta={
          <>
            {t("trackCount", { count: tracks.length })}
            {mix.created_at && ` · ${new Date(mix.created_at).toLocaleDateString(i18n.language)}`}
          </>
        }
        actions={
          <>
            <Button size="sm" onClick={() => playMixFrom()} className="gap-2">
              <Play size={14} fill="currentColor" strokeWidth={0} />
              {t("play")}
            </Button>
            {tracks.length > 0 && (
              <Button size="icon-sm" variant="outline" asChild>
                <a
                  href={`/api/v1/mixes/${encodeURIComponent(mixId)}/download`}
                  download
                  aria-label={t("actions.download", { ns: "common" })}
                  title={t("actions.download", { ns: "common" })}
                >
                  <Download size={14} />
                </a>
              </Button>
            )}
            {!isSaved && (
              <Button size="sm" variant="outline" onClick={handleSave} className="gap-2">
                <Bookmark size={14} />
                {t("actions.save", { ns: "common" })}
              </Button>
            )}
          </>
        }
      />

      {/* Tracks */}
      <div className="px-4 sm:px-6">
        <VirtualTrackList
          tracks={tracks}
          sourceLabel={mix.title}
          onPlayTrack={(trackId) => playMixFrom(trackId)}
        />
      </div>
    </div>
  )
}
