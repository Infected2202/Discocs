import { useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
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
  label: (id: number | string) => `/labels/${id}`,
  shelf: (id: number | string) => `/shelf/${id}`,
  user: (id: number | string) => `/u/${encodeURIComponent(String(id))}`,
} as const

export interface MediaCardProps {
  readonly id: number | string
  /** `user` — a discocs user (people shelf): round avatar, never a play button; `id` is the username. */
  readonly type: "artist" | "release" | "generated_mix" | "track" | "playlist" | "label" | "shelf" | "static" | "user"
  readonly title: string
  readonly subtitle?: string | null
  readonly subtitleLinks?: SubtitleLink[]
  readonly href?: string
  readonly reason?: string | null
  /** Appended to the subtitle line after " · " (e.g. relative play time on the History shelf). */
  readonly meta?: string | null
  /** Pulsing green dot before the subtitle — "playing right now" (user cards). */
  readonly live?: boolean
  readonly artwork?: ImageRef | null
  readonly artworkNode?: React.ReactNode
  readonly onPlay?: () => void
  readonly className?: string
  readonly variant?: "default" | "shelf"
  readonly disabled?: boolean
}

export default function MediaCard({
  id, type, title, subtitle, subtitleLinks, href, meta, live = false, artwork, artworkNode, onPlay,
  className, variant = "default", disabled = false,
}: MediaCardProps) {
  const { t } = useTranslation("media")
  const navigate = useNavigate()
  const isShelf = variant === "shelf"
  const hasSubtitleLinks = (subtitleLinks?.length ?? 0) > 0
  const canPlay = Boolean(onPlay) && !disabled && type !== "user"
  const metaSuffix = meta ? ` · ${meta}` : null
  const artworkContent = artworkNode ?? (
    <ArtworkImage
      src={artwork?.url}
      alt={title}
      className={cn(
        "w-full aspect-square",
        type === "artist" || type === "user" ? "rounded-full" : "rounded-md",
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

        {canPlay && (
          <button
            type="button"
            onClick={handlePlay}
            className="absolute bottom-2 right-2 flex h-9 w-9 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg opacity-0 translate-y-1 transition-all group-hover:opacity-100 group-hover:translate-y-0"
            aria-label={t("card.play", { title })}
          >
            <Play size={16} fill="currentColor" strokeWidth={0} />
          </button>
        )}

        {disabled && (
          <span className="absolute bottom-2 right-2 rounded bg-black/30 px-1.5 py-0.5 text-[10px] text-white/60">
            {t("card.soon")}
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
            {metaSuffix}
          </p>
        ) : subtitle || meta ? (
          <p className="mt-[4px] flex min-w-0 items-center gap-1 text-[12px] leading-none text-muted-foreground">
            {live && <LiveDot label={t("card.nowPlaying")} />}
            <span className="min-w-0 truncate">{subtitle ? `${subtitle}${metaSuffix ?? ""}` : meta}</span>
          </p>
        ) : null}
      </div>
    </div>
  )
}

/** Small pulsing green dot (the ping is skipped under prefers-reduced-motion). */
function LiveDot({ label }: { readonly label: string }) {
  return (
    <span className="relative flex h-1.5 w-1.5 shrink-0" title={label} data-testid="live-dot">
      <span aria-hidden="true" className="absolute inline-flex h-full w-full rounded-full bg-green-500 opacity-75 motion-safe:animate-ping" />
      <span aria-hidden="true" className="relative inline-flex h-1.5 w-1.5 rounded-full bg-green-500" />
      <span className="sr-only">{label}</span>
    </span>
  )
}
