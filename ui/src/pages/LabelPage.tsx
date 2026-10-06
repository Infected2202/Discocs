import { useLayoutEffect, useRef, useState } from "react"
import { Link, useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { ExternalLink, Shuffle } from "lucide-react"
import { useLabel, useLabelReleases, useToggleLabelLike } from "@/api/hooks/useLabel"
import { isNetworkError } from "@/lib/apiErrorKind"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ArtworkImage from "@/components/media/ArtworkImage"
import CollectionHeader from "@/components/media/CollectionHeader"
import GenreTags from "@/components/media/GenreTags"
import LikeButton from "@/components/media/LikeButton"
import Shelf from "@/components/media/Shelf"
import { usePlayerStore } from "@/store/playerStore"
import type { LabelDescriptionSegment, LabelDetail, LabelReleaseSort, ReleaseSummary } from "@/api/types"

const SORTS: LabelReleaseSort[] = ["release_date_desc", "release_date_asc", "popularity"]

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

  const hasLinks = label.links.length > 0
  // Ссылки лежат под спойлером вместе с остатком текста: кнопка нужна, если текст
  // обрезан или есть ссылки. Без текста кнопка называется просто «Ссылки».
  const toggleText = expanded
    ? t("description.showLess")
    : description ? t("description.showMore") : t("links")

  return (
    <section className="px-4 sm:px-6 pb-6 max-w-3xl space-y-3" aria-label={t("about")}>
      <div className="space-y-1">
        {description && (
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
        )}

        {expanded && hasLinks && (
          <ul className="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-xs" aria-label={t("links")}>
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

        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {(overflowing || hasLinks || expanded) && (
            <button
              type="button"
              aria-expanded={expanded}
              className="font-medium text-foreground/80 hover:text-foreground hover:underline"
              onClick={() => setExpanded((value) => !value)}
            >
              {toggleText}
            </button>
          )}
          {/* Источник показываем только у текста из Википедии/Discogs/Beatport;
              описание, написанное вручную, идёт без подписи. */}
          {description?.source && description.source !== "editorial" && (
            <span>
              {t("description.source", { source: t(`description.sources.${description.source}`) })}
            </span>
          )}
        </div>
      </div>
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
  const toggleLike = useToggleLabelLike(labelId)

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
  const groups = releasesData?.groups ?? []

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
        title={label.name}
        meta={
          <>
            {t("releaseCount", { count: label.release_count })}
            <GenreTags genres={label.genres} />
          </>
        }
        actions={
          <>
            <Button
              size="icon-sm"
              variant="outline"
              aria-label={t("shuffle")}
              title={t("shuffle")}
              disabled={label.release_count === 0}
              onClick={() => playSource("label", label.id, label.name, undefined, { shuffle: true })}
            >
              <Shuffle size={14} />
            </Button>
            <LikeButton
              entity="label"
              id={label.id}
              liked={label.liked}
              onToggle={() => toggleLike.mutate(!label.liked)}
              variant="control"
              size={18}
              title={t("like")}
            />
            {/* Сортировка — в шапке, а не отдельной строкой над группами:
                строка «Релизы ———» выглядела пустой группой. */}
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
          </>
        }
      />

      <LabelDescription label={label} />

      {!releasesLoading && groups.length === 0 ? (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("empty")}</p>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <Shelf
              key={group.key}
              grid
              title={t(`groups.${group.key}`)}
              items={group.items.map((release) => ({
                id: release.id,
                type: "release" as const,
                title: release.title,
                subtitle: releaseSubtitle(release),
                artwork: release.artwork,
                onPlay: () => playSource("release", release.id, release.title),
              }))}
            />
          ))}
        </div>
      )}
    </div>
  )
}
