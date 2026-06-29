import { useNavigate } from "react-router"
import { Play } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import type { ImageRef, SubtitleLink } from "@/api/types"

export interface MediaCardProps {
  id: number | string
  type: "artist" | "release" | "generated_mix" | "track" | "playlist" | "shelf" | "static"
  title: string
  subtitle?: string | null
  subtitleLinks?: SubtitleLink[]
  href?: string
  reason?: string | null
  artwork?: ImageRef | null
  artworkNode?: React.ReactNode
  onPlay?: () => void
  className?: string
  variant?: "default" | "shelf"
  disabled?: boolean
}

export default function MediaCard({
  id, type, title, subtitle, subtitleLinks, href, artwork, artworkNode, onPlay,
  className, variant = "default", disabled = false,
}: MediaCardProps) {
  const navigate = useNavigate()
  const isShelf = variant === "shelf"

  function handleClick() {
    if (disabled) return
    if (href) { navigate(href); return }
    if (type === "artist") navigate(`/artists/${id}`)
    else if (type === "release") navigate(`/releases/${id}`)
    else if (type === "generated_mix") navigate(`/mixes/${id}`)
    // track: no fallback — action.target from backend always provides the release URL
    else if (type === "playlist") navigate(`/playlists/${id}`)
    else if (type === "shelf") navigate(`/shelf/${id}`)
    // "static" — no navigation
  }

  function handlePlay(e: React.MouseEvent) {
    e.stopPropagation()
    onPlay?.()
  }

  return (
    <div
      className={cn(
        "group relative transition-colors",
        disabled ? "cursor-default" : "cursor-pointer",
        isShelf
          ? "min-w-0 overflow-hidden p-2 hover:bg-muted/40"
          : "w-36 sm:w-44 shrink-0 rounded-lg p-3 hover:bg-muted/60",
        className,
      )}
      onClick={handleClick}
      role={disabled ? undefined : "button"}
      tabIndex={disabled ? undefined : 0}
      onKeyDown={(e) => !disabled && e.key === "Enter" && handleClick()}
    >
      {/* Artwork */}
      <div className="relative mb-2">
        {artworkNode ?? (
          <ArtworkImage
            src={artwork?.url}
            alt={title}
            className={cn(
              "w-full aspect-square",
              type === "artist" ? "rounded-full" : "rounded-md",
            )}
            fallbackLetter={title[0]}
          />
        )}

        {/* Play button overlay */}
        {onPlay && !disabled && (
          <button
            onClick={handlePlay}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg opacity-0 translate-y-1 transition-all group-hover:opacity-100 group-hover:translate-y-0"
            aria-label={`Play ${title}`}
          >
            <Play size={16} fill="currentColor" strokeWidth={0} />
          </button>
        )}

        {disabled && (
          <span className="absolute bottom-2 right-2 text-[10px] text-white/60 bg-black/30 px-1.5 py-0.5 rounded">
            Soon
          </span>
        )}
      </div>

      {/* Text */}
      <div className="space-y-0.5 min-w-0">
        <p className="truncate text-sm font-medium leading-tight">{title}</p>
        {subtitleLinks && subtitleLinks.length > 0 ? (
          <p className="truncate text-xs text-muted-foreground">
            {subtitleLinks.map((link, i) => (
              <span key={link.href}>
                {i > 0 && ", "}
                <span
                  className="hover:text-foreground hover:underline cursor-pointer transition-colors"
                  onClick={(e) => { e.stopPropagation(); navigate(link.href) }}
                >
                  {link.label}
                </span>
              </span>
            ))}
          </p>
        ) : subtitle ? (
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
    </div>
  )
}
