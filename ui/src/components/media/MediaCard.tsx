import { useNavigate } from "react-router"
import { Play } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import type { ImageRef } from "@/api/types"

export interface MediaCardProps {
  id: number | string
  type: "artist" | "release" | "generated_mix"
  title: string
  subtitle?: string | null
  reason?: string | null
  artwork?: ImageRef | null
  onPlay?: () => void
  className?: string
}

export default function MediaCard({ id, type, title, subtitle, reason, artwork, onPlay, className }: MediaCardProps) {
  const navigate = useNavigate()

  function handleClick() {
    if (type === "artist") navigate(`/artists/${id}`)
    else if (type === "release") navigate(`/releases/${id}`)
    else if (type === "generated_mix") navigate(`/mixes/${id}`)
  }

  function handlePlay(e: React.MouseEvent) {
    e.stopPropagation()
    onPlay?.()
  }

  return (
    <div
      className={cn(
        "group relative w-44 shrink-0 cursor-pointer rounded-lg p-3 transition-colors hover:bg-muted/60",
        className,
      )}
      onClick={handleClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && handleClick()}
    >
      {/* Artwork */}
      <div className="relative mb-3">
        <ArtworkImage
          src={artwork?.url}
          alt={title}
          className={cn("w-full aspect-square", type === "artist" ? "rounded-full" : "rounded-md")}
          fallbackLetter={title[0]}
        />

        {/* Play button overlay */}
        {onPlay && (
          <button
            onClick={handlePlay}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg opacity-0 translate-y-1 transition-all group-hover:opacity-100 group-hover:translate-y-0"
            aria-label={`Play ${title}`}
          >
            <Play size={16} fill="currentColor" strokeWidth={0} />
          </button>
        )}
      </div>

      {/* Text */}
      <div className="space-y-0.5 min-w-0">
        <p className="truncate text-sm font-medium leading-tight">{title}</p>
        {subtitle && (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        )}
        {reason && (
          <p className="truncate text-xs text-muted-foreground/70 italic">{reason}</p>
        )}
      </div>
    </div>
  )
}
