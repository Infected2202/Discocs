import { useEffect, useRef } from "react"
import { useNavigate, useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { ChevronLeft } from "lucide-react"
import { useRefreshListensOnPlayChange, useUserListens } from "@/api/hooks/useProfile"
import { usePeople } from "@/api/hooks/usePeople"
import { isNetworkError } from "@/lib/apiErrorKind"
import { formatDayHeading, groupListensByDay, withoutCurrentPlay } from "@/lib/listenHistory"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import ListenRows from "@/components/profile/ListenRows"

function RowsSkeleton() {
  return (
    <div className="px-4 sm:px-6 space-y-3" data-testid="history-skeleton">
      {[1, 2, 3, 4, 5, 6].map((i) => (
        <Skeleton key={i} className="h-12 w-full rounded-md" />
      ))}
    </div>
  )
}

/** `/u/:username/history` — every listen, newest first, grouped by local day. */
export default function ListeningHistoryPage() {
  const { t, i18n } = useTranslation("user")
  const { username = "" } = useParams<{ username: string }>()
  const navigate = useNavigate()
  const sentinelRef = useRef<HTMLDivElement>(null)
  const { data, isLoading, error, hasNextPage, isFetchingNextPage, fetchNextPage } = useUserListens(username)
  const { data: people } = usePeople()
  const nowPlaying = people
    ? (people.items.find((p) => p.username.toLowerCase() === username.toLowerCase())?.now_playing ?? null)
    : undefined
  useRefreshListensOnPlayChange(username, nowPlaying)

  // Load the next page when the bottom comes into view (the button stays as a fallback).
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || typeof IntersectionObserver === "undefined") return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) void fetchNextPage()
      },
      { rootMargin: "300px" },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  const now = new Date()
  const playing = nowPlaying?.track ?? null
  const listens = withoutCurrentPlay(data?.pages.flatMap((page) => page.items) ?? [], playing, now)
  const total = data?.pages[0]?.total
  const groups = groupListensByDay(listens)

  return (
    <div className="py-6 space-y-6">
      <div className="px-4 sm:px-6 flex items-center gap-3 min-w-0">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="flex items-center justify-center w-8 h-8 shrink-0 rounded-full hover:bg-muted transition-colors"
          aria-label={t("actions.back", { ns: "common" })}
        >
          <ChevronLeft size={18} />
        </button>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold truncate">{t("history.title")}</h1>
          <p className="text-sm text-muted-foreground truncate">
            {username}
            {total !== undefined && ` · ${t("listenCount", { count: total, formatted: total.toLocaleString(i18n.language) })}`}
          </p>
        </div>
      </div>

      {isLoading && <RowsSkeleton />}

      {error && (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">
          {isNetworkError(error) ? t("status.reconnecting", { ns: "common" }) : t("notFound")}
        </p>
      )}

      {playing && !error && (
        <div className="px-4 sm:px-6">
          <ListenRows listens={[]} nowPlaying={playing} sourceLabel={t("history.title")} now={now} />
        </div>
      )}

      {!isLoading && !error && listens.length === 0 && !playing && (
        <p className="px-4 sm:px-6 text-sm text-muted-foreground">{t("empty.noListens")}</p>
      )}

      {groups.map((group) => (
        <section key={group.day} className="space-y-1" aria-label={formatDayHeading(group.day, i18n.language, now)}>
          <h2 className="px-4 sm:px-6 text-sm font-semibold">
            {formatDayHeading(group.day, i18n.language, now)}
          </h2>
          <div className="px-4 sm:px-6">
            <ListenRows listens={group.items} sourceLabel={t("history.title")} now={now} />
          </div>
        </section>
      ))}

      <div ref={sentinelRef} className="h-1" />

      {isFetchingNextPage && <RowsSkeleton />}
      {hasNextPage && !isFetchingNextPage && (
        <div className="px-4 sm:px-6">
          <Button variant="outline" size="sm" onClick={() => void fetchNextPage()}>
            {t("history.loadMore")}
          </Button>
        </div>
      )}
    </div>
  )
}
