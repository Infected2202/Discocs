import { Download, MoreHorizontal } from "lucide-react"
import { useTranslation } from "react-i18next"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

interface ShareDownloadMenuProps {
  /** Public capability URL the download link points at. */
  readonly downloadUrl: string
  /** Distinguishes a single track from the whole shared list. */
  readonly scope: "track" | "collection"
  /** Names the subject in the trigger's accessible label. */
  readonly label: string
  readonly className?: string
  readonly iconSize?: number
}

/**
 * The public player's only context menu. It deliberately shares nothing with
 * the authenticated `TrackMenu`: every other entry there (queue, likes,
 * playlists, instant mix, re-share) needs a signed-in user, and a guest has
 * none. Keeping it separate means no personal action can leak onto a page
 * anyone with the link can open.
 */
export default function ShareDownloadMenu({
  downloadUrl,
  scope,
  label,
  className,
  iconSize = 15,
}: ShareDownloadMenuProps) {
  const { t } = useTranslation("share")

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={t(scope === "track" ? "menu.trackActions" : "menu.collectionActions", { name: label })}
        className={cn(
          "grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          className,
        )}
        onClick={(event) => event.stopPropagation()}
      >
        <MoreHorizontal size={iconSize} />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          {/* A plain link, not a fetch: the browser owns the progress UI, the
              Save dialog and resuming, and the capability token is already in
              the URL, so no header or credential has to be attached. */}
          <a href={downloadUrl} download>
            <Download size={14} className="mr-2" />
            {t(scope === "track" ? "menu.downloadTrack" : "menu.downloadAll")}
          </a>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
