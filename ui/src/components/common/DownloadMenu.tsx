import { Download } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

interface DownloadMenuProps {
  /** Download endpoint of the collection; `?format=` selects the encoding. */
  readonly href: string
}

/**
 * Download button of a release / playlist / mix header. It asks which file to
 * hand out instead of hiding the choice in a profile setting: the original
 * (as stored, usually FLAC) or a re-encoded MP3 320 for a phone or a friend.
 * Plain links, not fetches — the browser owns the progress UI and Save dialog.
 */
export default function DownloadMenu({ href }: DownloadMenuProps) {
  const { t } = useTranslation("common")
  const label = t("actions.download")

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="icon-sm" variant="outline" aria-label={label} title={label}>
          <Download size={14} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem asChild>
          <a href={href} download>
            {t("actions.downloadOriginal")}
          </a>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={`${href}?format=mp3`} download>
            {t("actions.downloadMp3")}
          </a>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
