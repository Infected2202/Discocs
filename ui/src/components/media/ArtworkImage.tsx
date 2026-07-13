import { useState } from "react"
import { useTranslation } from "react-i18next"
import { cn } from "@/lib/utils"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"

interface ArtworkImageProps {
  readonly src: string | null | undefined
  readonly alt: string
  readonly size?: number
  readonly className?: string
  readonly fallbackLetter?: string
  /** Clicking the image opens it full-size in a modal. */
  readonly expandable?: boolean
}

export default function ArtworkImage({ src, alt, size, className, fallbackLetter, expandable }: ArtworkImageProps) {
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const { t } = useTranslation("common")
  const letter = fallbackLetter ?? (alt?.[0] ?? "?").toUpperCase()

  if (!src || failed) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded bg-muted text-muted-foreground font-semibold select-none shrink-0",
          className,
        )}
        style={size ? { width: size, height: size, fontSize: size * 0.4 } : undefined}
        aria-label={alt}
      >
        {letter}
      </div>
    )
  }

  const img = (
    <img
      src={src}
      alt={alt}
      className={cn("rounded object-cover shrink-0", className)}
      style={size ? { width: size, height: size } : undefined}
      onError={() => setFailed(true)}
      loading="lazy"
    />
  )

  if (!expandable) return img

  return (
    <>
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="block shrink-0 cursor-zoom-in rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={t("actions.viewFullSize")}
      >
        {img}
      </button>
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent
          showCloseButton
          className="flex w-auto max-w-[min(92vw,92vh)] items-center justify-center bg-transparent p-0 shadow-none ring-0 sm:max-w-[min(92vw,92vh)]"
        >
          <DialogTitle className="sr-only">{alt}</DialogTitle>
          <img
            src={src}
            alt={alt}
            className="max-h-[92vh] max-w-[92vw] w-auto h-auto rounded-lg object-contain"
          />
        </DialogContent>
      </Dialog>
    </>
  )
}
