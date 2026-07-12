import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

interface CollectionHeaderProps {
  /** Rendered above the header row — e.g. a "Back" button. */
  readonly above?: ReactNode
  /** Left-hand artwork element: ArtworkImage, a round avatar, a gradient tile… */
  readonly artwork: ReactNode
  /** Small uppercase label above the title (release type, "Playlist", "Generated mix"). */
  readonly kicker?: ReactNode
  readonly title: string
  /**
   * Truncate the title to one line on overflow. On by default; turn off for
   * user-authored titles (playlists) that should wrap instead of clip.
   */
  readonly truncateTitle?: boolean
  /** Muted metadata under the title (artists, counts, description…). */
  readonly meta?: ReactNode
  /** Primary action cluster — Play / Shuffle / Like / Save / … */
  readonly actions?: ReactNode
}

/**
 * Shared header for every collection page (release, playlist, mix, artist):
 * a responsive artwork + title + meta + actions layout. Pages supply the
 * artwork, meta, and action nodes; this component owns the layout, kicker,
 * and title typography so the four pages can't drift apart.
 */
export default function CollectionHeader({
  above,
  artwork,
  kicker,
  title,
  truncateTitle = true,
  meta,
  actions,
}: CollectionHeaderProps) {
  return (
    <div className="px-4 sm:px-6 pt-8 pb-4 space-y-6">
      {above}
      <div className="flex flex-col sm:flex-row gap-4 sm:gap-6 items-start sm:items-end">
        {artwork}
        <div className="pb-0 sm:pb-2 min-w-0">
          {kicker && (
            <p className="text-xs text-muted-foreground uppercase tracking-wide mb-1">{kicker}</p>
          )}
          <h1 className={cn("text-3xl font-bold", truncateTitle && "truncate")}>{title}</h1>
          {meta && <div className="text-sm text-muted-foreground mt-1">{meta}</div>}
          {actions && <div className="mt-4 flex gap-2 items-center">{actions}</div>}
        </div>
      </div>
    </div>
  )
}
