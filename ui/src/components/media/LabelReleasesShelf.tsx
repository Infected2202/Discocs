import { useTranslation } from "react-i18next"
import { useLabelReleases } from "@/api/hooks/useLabel"
import Shelf from "@/components/media/Shelf"
import { usePlayerStore } from "@/store/playerStore"

const PREVIEW = 16

interface LabelReleasesShelfProps {
  readonly label: { id: number; name: string }
  readonly releaseId: number
  /** Артисты релиза: их релизы уже на полке «Ещё от этих артистов». */
  readonly artistIds: readonly number[]
}

/**
 * Полка «От лейбла …» внизу страницы релиза: популярные релизы лейбла других
 * артистов. Пусто (лейбл самого артиста) — полки нет; «Ещё» ведёт на страницу лейбла.
 */
export default function LabelReleasesShelf({ label, releaseId, artistIds }: LabelReleasesShelfProps) {
  const { t } = useTranslation("release")
  const playSource = usePlayerStore((s) => s.playSource)
  const { data } = useLabelReleases(label.id, "popularity")
  const own = new Set(artistIds)
  const others = (data?.items ?? []).filter(
    (r) => r.id !== releaseId && !r.artists.some((a) => own.has(a.id)),
  )
  if (others.length === 0) return null

  return (
    <Shelf
      title={t("fromLabel", { name: label.name })}
      total={others.length}
      moreHref={`/labels/${label.id}`}
      items={others.slice(0, PREVIEW).map((r) => ({
        id: r.id,
        type: "release" as const,
        title: r.title,
        subtitle: r.artists.map((a) => a.name).join(", "),
        artwork: r.artwork,
        onPlay: () => playSource("release", r.id, r.title),
      }))}
    />
  )
}
