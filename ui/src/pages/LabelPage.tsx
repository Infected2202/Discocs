import { useLayoutEffect, useRef, useState } from "react"
import { Link, useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { ExternalLink } from "lucide-react"
import { useLabel, useLabelReleases } from "@/api/hooks/useLabel"
import { isNetworkError } from "@/lib/apiErrorKind"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import Shelf from "@/components/media/Shelf"
import { usePlayerStore } from "@/store/playerStore"
import type { LabelDescriptionSegment, LabelDetail, LabelReleaseSort, ReleaseSummary } from "@/api/types"

const SORTS: LabelReleaseSort[] = ["release_date_desc", "release_date_asc"]

function releaseSubtitle(release: ReleaseSummary): string | null {
  const artists = release.artists.map((artist) => artist.name).join(", ")
  const year = release.release_year ? String(release.release_year) : ""
  return [artists, year].filter(Boolean).join(" · ") || null
}

/** Segments keyed by their character offset in the text — stable and unique. */
function keyedSegments(segments: LabelDescriptionSegment[]) {
  let offset = 0
  return segments.map((segment) => {
    const key = `${offset}-${segment.type}`
    offset += segment.text.length
    return { key, segment }
  })
}

function linkTitle(link: { url: string; title?: string | null }): string {
  if (link.title) return link.title
  try {
    return new URL(link.url).hostname.replace(/^www\./, "")
  } catch {
    return link.url
  }
}

function LabelDescription({ label }: { readonly label: LabelDetail }) {
  const { t } = useTranslation("label")
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const textRef = useRef<HTMLParagraphElement>(null)
  const description = label.description

  // Кнопка «показать полностью» нужна, только если свёрнутый текст обрезан.
  useLayoutEffect(() => {
    const el = textRef.current
    if (!el || expanded) return
    setOverflowing(el.scrollHeight > el.clientHeight + 1)
  }, [description, expanded])

  if (!description && label.links.length === 0) return null

  return (
    <section className="px-4 sm:px-6 pb-6 max-w-3xl space-y-3" aria-label={t("kicker")}>
      {description && (
        <div className="space-y-1">
          <p
            ref={textRef}
            data-testid="label-description"
            className={cn(
              "text-sm leading-relaxed whitespace-pre-line text-foreground/90",
              !expanded && "line-clamp-4",
            )}
          >
            {keyedSegments(description.segments).map(({ key, segment }) =>
              segment.type === "artist" && segment.artist_id !== null ? (
                <Link
                  key={key}
                  to={`/artists/${segment.artist_id}`}
                  className="font-medium hover:underline"
                >
                  {segment.text}
                </Link>
              ) : (
                <span key={key}>{segment.text}</span>
              ),
            )}
          </p>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {(overflowing || expanded) && (
              <button
                type="button"
                className="font-medium text-foreground/80 hover:text-foreground hover:underline"
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? t("description.showLess") : t("description.showMore")}
              </button>
            )}
            {description.source && (
              <span>
                {t("description.source", { source: t(`description.sources.${description.source}`) })}
              </span>
            )}
          </div>
        </div>
      )}

      {label.links.length > 0 && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label={t("links")}>
          {label.links.map((link) => (
            <li key={link.url}>
              <a
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground hover:underline"
              >
                {linkTitle(link)}
                <ExternalLink size={11} />
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function LabelPageSkeleton() {
  return (
    <div className="space-y-8">
      <div className="px-4 sm:px-6 pt-8 flex gap-6 items-end">
        <Skeleton className="w-36 h-36 rounded-md shrink-0" />
        <div className="space-y-3 pb-2">
          <Skeleton className="h-9 w-64" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
      <div className="px-4 sm:px-6 space-y-3">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-4 w-full max-w-3xl" />
        ))}
      </div>
    </div>
  )
}

export default function LabelPage() {
  const { t } = useTranslation("label")
  const { id } = useParams<{ id: string }>()
  const labelId = Number(id)
  const [sort, setSort] = useState<LabelReleaseSort>("release_date_desc")
  const { data: labelData, isLoading, error } = useLabel(labelId)
  const { data: releasesData, isLoading: releasesLoading } = useLabelReleases(labelId, sort)
  const playSource = usePlayerStore((s) => s.playSource)

  if (isLoading) return <LabelPageSkeleton />
  if (error || !labelData) {
    if (error && isNetworkError(error)) {
      return (
        <div className="p-8">
          <p className="text-muted-foreground text-sm">{t("status.reconnecting", { ns: "common" })}</p>
        </div>
      )
    }
    return (
      <div className="p-8">
        <p className="text-destructive text-sm">{t("notFound")}</p>
      </div>
    )
  }

  const { label } = labelData
  const releases = releasesData?.items ?? []

  return (
    <div className="relative pb-8">
      <CollectionHeader
        artwork={
          <ArtworkImage
            src={label.artwork.url}
            alt={label.name}
            size={144}
            className="rounded-md shrink-0"
            fallbackLetter={label.name[0]}
            expandable
          />
        }
        kicker={t("kicker")}
        title={label.name}
        meta={t("releaseCount", { count: label.release_count })}
      />

      <LabelDescription label={label} />

      <div className="px-4 sm:px-6 pb-2 flex items-center gap-3">
        <h2 className="text-sm font-semibold">{t("releases")}</h2>
        <div aria-hidden="true" className="h-px min-w-3 flex-1 bg-primary/50" />
        <select
          aria-label={t("sort.label")}
          value={sort}
          onChange={(event) => setSort(event.target.value as LabelReleaseSort)}
          className="h-8 rounded-md border border-foreground/15 bg-background px-2 text-xs"
        >
          {SORTS.map((value) => (
            <option key={value} value={value}>{t(`sort.${value}`)}</option>
          ))}
        </select>
      </div>

      {!releasesLoading && releases.length === 0 ? (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <Shelf
          grid
          items={releases.map((release) => ({
            id: release.id,
            type: "release" as const,
            title: release.title,
            subtitle: releaseSubtitle(release),
            artwork: release.artwork,
            onPlay: () => playSource("release", release.id, release.title),
          }))}
        />
      )}
    </div>
  )
}
