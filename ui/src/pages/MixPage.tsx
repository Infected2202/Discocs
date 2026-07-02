import { useParams } from "react-router"
import { Play, Bookmark } from "lucide-react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useMix } from "@/api/hooks/useMix"
import { playMix, saveMix } from "@/api/mixes"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
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
  const { id } = useParams<{ id: string }>()
  const mixId = id!
  const queryClient = useQueryClient()
  const { data: mix, isLoading, error } = useMix(mixId)
  const playFromEnvelope = usePlayerStore((s) => s.playFromEnvelope)

  const { mutate: handleSave, isPending: saving } = useMutation({
    mutationFn: () => saveMix(mixId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["mix", mixId] }),
  })

  if (isLoading) return <MixPageSkeleton />
  if (error || !mix) {
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">Mix not found.</p>
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
      <div className="px-4 sm:px-6 pt-8 flex flex-col sm:flex-row gap-4 sm:gap-6 items-start sm:items-end">
        <ArtworkImage
          src={mix.artwork?.url}
          alt={mix.title}
          size={176}
          className="rounded-lg shrink-0"
          fallbackLetter="M"
        />
        <div className="pb-0 sm:pb-2 min-w-0">
          <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1">
            Generated mix
          </p>
          <h1 className="text-3xl font-bold truncate">{mix.title}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {tracks.length} tracks
            {mix.created_at && ` · ${new Date(mix.created_at).toLocaleDateString()}`}
          </p>

          <div className="flex gap-2 mt-4">
            <Button
              size="sm"
              onClick={() => playMix(mixId).then(playFromEnvelope)}
              className="gap-2"
            >
              <Play size={14} fill="currentColor" strokeWidth={0} />
              Play
            </Button>
            {!isSaved && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => handleSave()}
                disabled={saving}
                className="gap-2"
              >
                <Bookmark size={14} />
                {saving ? "Saving…" : "Save"}
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Tracks */}
      <div className="px-4 sm:px-6">
        <VirtualTrackList tracks={tracks} sourceLabel={mix.title} />
      </div>
    </div>
  )
}
