import { useState } from "react"
import { cn } from "@/lib/utils"

interface ArtworkImageProps {
  readonly src: string | null | undefined
  readonly alt: string
  readonly size?: number
  readonly className?: string
  readonly fallbackLetter?: string
}

export default function ArtworkImage({ src, alt, size, className, fallbackLetter }: ArtworkImageProps) {
  const [failed, setFailed] = useState(false)
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

  return (
    <img
      src={src}
      alt={alt}
      className={cn("rounded object-cover shrink-0", className)}
      style={size ? { width: size, height: size } : undefined}
      onError={() => setFailed(true)}
      loading="lazy"
    />
  )
}
