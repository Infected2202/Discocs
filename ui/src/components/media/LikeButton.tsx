import { ThumbsUp, Heart } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavidromeStore } from "@/store/navidromeStore"

export type LikeEntity = "track" | "album" | "artist"

interface LikeButtonProps {
  readonly entity: LikeEntity
  readonly id: number
  /**
   * "control" — always visible, icon fills when liked (player, page headers).
   * "row" — hidden until the row is hovered on desktop, colour-only (list rows).
   * Row variant relies on an ancestor marked `group/row`.
   */
  readonly variant?: "control" | "row"
  readonly size?: number
  readonly title?: string
  readonly className?: string
}

// Tracks use thumbs-up (recommendation feedback); albums/artists use heart
// (save to favourites). Preserved from the original per-site markup.
const ICON = { track: ThumbsUp, album: Heart, artist: Heart } as const

export default function LikeButton({
  entity,
  id,
  variant = "control",
  size = 15,
  title = "Like",
  className,
}: LikeButtonProps) {
  const liked = useNavidromeStore((s) =>
    entity === "track"
      ? s.likedIds.has(id)
      : entity === "album"
        ? s.likedAlbumIds.has(id)
        : s.likedArtistIds.has(id),
  )
  const toggle = useNavidromeStore((s) =>
    entity === "track" ? s.toggleLike : entity === "album" ? s.toggleAlbumLike : s.toggleArtistLike,
  )

  const Icon = ICON[entity]
  // Only the control variant fills the glyph — list rows stay colour-only so a
  // filled icon doesn't fight the row's own active/hover states.
  const filled = variant === "control" && liked

  return (
    <button
      type="button"
      title={title}
      onClick={(event) => {
        event.stopPropagation()
        toggle(id)
      }}
      className={cn(
        "flex items-center justify-center rounded transition-colors",
        variant === "control"
          ? "p-1.5"
          : "mx-auto h-7 w-7",
        liked
          ? "text-primary"
          : variant === "control"
            ? "text-muted-foreground hover:text-foreground"
            : "text-muted-foreground hover:text-foreground md:opacity-0 md:group-hover/row:opacity-100",
        className,
      )}
    >
      <Icon size={size} fill={filled ? "currentColor" : "none"} />
    </button>
  )
}
