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
  /** У релиза несколько лейблов — в заголовке нужно название, иначе «От этого лейбла». */
  readonly named: boolean
}

/**
 * Полка «От этого лейбла» на странице релиза: популярные релизы лейбла других
 * артистов. Пусто (лейбл самого артиста) — полки нет; «Ещё» ведёт на страницу лейбла.
 */
export default function LabelReleasesShelf({ label, releaseId, artistIds, named }: LabelReleasesShelfProps) {
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
      title={named ? t("fromNamedLabel", { name: label.name }) : t("fromThisLabel")}
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
