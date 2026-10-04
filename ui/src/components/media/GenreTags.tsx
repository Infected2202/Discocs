import { useTranslation } from "react-i18next"
import { Badge } from "@/components/ui/badge"
import type { GenreCount } from "@/api/types"

interface GenreTagsProps {
  /** Styles strongest first: plain names (release) or with release counts (artist, label). */
  readonly genres: readonly (string | GenreCount)[] | undefined
}

/** Row of genre_discogs400 styles under a collection header; renders nothing without genres. */
export default function GenreTags({ genres }: GenreTagsProps) {
  const { t } = useTranslation("common")
  if (!genres?.length) return null
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={t("genres")}>
      {genres.map((genre) => {
        const name = typeof genre === "string" ? genre : genre.name
        return (
          <li key={name}>
            <Badge variant="secondary" className="font-normal">
              {name}
              {typeof genre !== "string" && (
                <span className="text-muted-foreground tabular-nums">{genre.release_count}</span>
              )}
            </Badge>
          </li>
        )
      })}
    </ul>
  )
}
