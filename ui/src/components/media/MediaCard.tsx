import { useNavigate } from "react-router"
import { Play } from "lucide-react"
import { cn } from "@/lib/utils"
import ArtworkImage from "./ArtworkImage"
import TiltedArtwork from "./TiltedArtwork"
import type { ImageRef, SubtitleLink } from "@/api/types"

const ENTITY_HREFS = {
  artist: (id: number | string) => `/artists/${id}`,
  release: (id: number | string) => `/releases/${id}`,
  generated_mix: (id: number | string) => `/mixes/${id}`,
  playlist: (id: number | string) => `/playlists/${id}`,
  shelf: (id: number | string) => `/shelf/${id}`,
} as const

export interface MediaCardProps {
  readonly id: number | string
  readonly type: "artist" | "release" | "generated_mix" | "track" | "playlist" | "shelf" | "static"
  readonly title: string
  readonly subtitle?: string | null
  readonly subtitleLinks?: SubtitleLink[]
  readonly href?: string
  readonly reason?: string | null
  readonly artwork?: ImageRef | null
  readonly artworkNode?: React.ReactNode
  readonly onPlay?: () => void
  readonly className?: string
  readonly variant?: "default" | "shelf"
  readonly disabled?: boolean
}

export default function MediaCard({
  id, type, title, subtitle, subtitleLinks, href, artwork, artworkNode, onPlay,
  className, variant = "default", disabled = false,
}: MediaCardProps) {
  const navigate = useNavigate()
  const isShelf = variant === "shelf"
  const hasSubtitleLinks = (subtitleLinks?.length ?? 0) > 0
  const artworkContent = artworkNode ?? (
    <ArtworkImage
      src={artwork?.url}
      alt={title}
      className={cn(
        "w-full aspect-square",
        type === "artist" ? "rounded-full" : "rounded-md",
      )}
      fallbackLetter={title[0]}
    />
  )
  const renderedArtwork = isShelf ? <TiltedArtwork>{artworkContent}</TiltedArtwork> : artworkContent

  function handleClick() {
    if (disabled) return
    if (href) {
      navigate(href)
      return
    }

    const fallbackHref = ENTITY_HREFS[type as keyof typeof ENTITY_HREFS]?.(id)

    if (fallbackHref) {
      navigate(fallbackHref)
    }
  }

  function handlePlay(event: React.MouseEvent) {
    event.stopPropagation()
    onPlay?.()
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (disabled) return
    if (event.key !== "Enter" && event.key !== " ") return
    event.preventDefault()
    handleClick()
  }

  function handleSubtitleClick(event: React.MouseEvent<HTMLButtonElement>, targetHref: string) {
    event.stopPropagation()
    navigate(targetHref)
  }

  return (
    <div
      className={cn(
        "group relative transition-colors",
        disabled ? "cursor-default" : "cursor-pointer",
        isShelf
          ? "min-w-0 p-2 hover:z-10 hover:bg-muted/40 transition-colors duration-300"
          : "w-36 sm:w-44 shrink-0 rounded-lg p-2 hover:bg-muted/60",
        className,
      )}
      onClick={handleClick}
      role={disabled ? undefined : "button"}
      tabIndex={disabled ? undefined : 0}
      aria-disabled={disabled || undefined}
      onKeyDown={handleKeyDown}
    >
      <div className="relative mb-[5px]">
        {renderedArtwork}

        {onPlay && !disabled && (
          <button
            type="button"
            onClick={handlePlay}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg opacity-0 translate-y-1 transition-all group-hover:opacity-100 group-hover:translate-y-0"
            aria-label={`Play ${title}`}
          >
            <Play size={16} fill="currentColor" strokeWidth={0} />
          </button>
        )}

        {disabled && (
          <span className="absolute bottom-2 right-2 rounded bg-black/30 px-1.5 py-0.5 text-[10px] text-white/60">
            Soon
          </span>
        )}
      </div>

      <div className="min-w-0">
        <p className="truncate text-[14px] font-medium leading-none">{title}</p>
        {hasSubtitleLinks ? (
          <p className="truncate text-[10px] text-muted-foreground">
            {subtitleLinks?.map((link, index) => (
              <span key={link.href}>
                {index > 0 && ", "}
                <button
                  type="button"
                  className="cursor-pointer transition-colors hover:text-foreground hover:underline"
                  onClick={(event) => handleSubtitleClick(event, link.href)}
                >
                  {link.label}
                </button>
              </span>
            ))}
          </p>
        ) : subtitle ? (
          <p className="mt-[4px] truncate text-[12px] leading-none text-muted-foreground">{subtitle}</p>
        ) : null}
      </div>
    </div>
  )
}
