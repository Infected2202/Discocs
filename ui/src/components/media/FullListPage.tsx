import { useEffect, useRef } from "react"
import { useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
import { ChevronLeft } from "lucide-react"
import MediaCard, { type MediaCardProps } from "./MediaCard"
import VirtualCardGrid from "./VirtualCardGrid"
import { Skeleton } from "@/components/ui/skeleton"
import type { PagedList } from "@/lib/shelves"

/** What FullListPage needs from an infinite query (usePagedList / useShelf). */
export interface PagedListSource<T> {
  readonly data?: { readonly pages: ReadonlyArray<PagedList<T>> }
  readonly isLoading: boolean
  readonly isFetchingNextPage: boolean
  readonly hasNextPage: boolean
  readonly fetchNextPage: () => unknown
}

interface FullListPageProps<T> {
  readonly title: string
  /** Second header line, e.g. the artist or the profile the list belongs to. */
  readonly subtitle?: string | null
  readonly source: PagedListSource<T>
  readonly getKey: (item: T) => string
  readonly toCard: (item: T) => MediaCardProps
}

function CardSkeletons({ count }: { readonly count: number }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-1 px-3">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="p-3 space-y-3">
          <Skeleton className="w-full aspect-square rounded-md" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  )
}

/**
 * The full list behind a horizontal shelf's «Ещё» (docs/web-ui.md «Полки»):
 * back button, title with the total, a virtualized card grid and infinite
 * scroll over any paginated source. Route pages only plug in the source and
 * the item → card mapping.
 */
/** Back button, title, optional subtitle and the item count of a full list. */
export function FullListHeader({ title, subtitle, total }: {
  readonly title: string
  readonly subtitle?: string | null
  readonly total?: number
}) {
  const { t } = useTranslation("media")
  const navigate = useNavigate()
  return (
    <div className="px-4 sm:px-6 flex items-center gap-3 min-w-0">
      <button
        type="button"
        onClick={() => navigate(-1)}
        className="flex items-center justify-center w-8 h-8 rounded-full hover:bg-muted transition-colors shrink-0"
        aria-label={t("actions.back", { ns: "common" })}
      >
        <ChevronLeft size={18} />
      </button>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold truncate">{title}</h1>
        {subtitle && <p className="text-sm text-muted-foreground truncate">{subtitle}</p>}
      </div>
      {total !== undefined && (
        <span className="text-sm text-muted-foreground shrink-0">{t("itemCount", { count: total })}</span>
      )}
    </div>
  )
}

/** Calls `fetchNextPage` when the returned sentinel scrolls near the viewport. */
export function useInfiniteScrollSentinel(
  { hasNextPage, isFetchingNextPage, fetchNextPage }: Pick<PagedListSource<unknown>, "hasNextPage" | "isFetchingNextPage" | "fetchNextPage">,
) {
  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { rootMargin: "200px" },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])
  return sentinelRef
}

export default function FullListPage<T>({ title, subtitle, source, getKey, toCard }: FullListPageProps<T>) {
  const { data, isLoading, isFetchingNextPage, hasNextPage, fetchNextPage } = source

  const sentinelRef = useInfiniteScrollSentinel({ hasNextPage, isFetchingNextPage, fetchNextPage })

  const firstPage = data?.pages[0]
  const allItems = data?.pages.flatMap((page) => page.items) ?? []

  return (
    <div className="py-6 space-y-6">
      <FullListHeader title={title} subtitle={subtitle} total={firstPage?.total} />

      {/* Grid — virtualized so only visible cards (and their images) stay in DOM */}
      {isLoading ? (
        <CardSkeletons count={16} />
      ) : (
        <div className="px-3">
          <VirtualCardGrid
            items={allItems}
            getKey={getKey}
            renderItem={(item) => <MediaCard {...toCard(item)} variant="shelf" className="w-full" />}
          />
        </div>
      )}

      {/* Infinite scroll sentinel */}
      <div ref={sentinelRef} className="h-4" data-testid="full-list-sentinel" />

      {isFetchingNextPage && <CardSkeletons count={8} />}
    </div>
  )
}
