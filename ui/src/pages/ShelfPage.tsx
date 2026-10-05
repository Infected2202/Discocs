import { useParams } from "react-router"
import { useTranslation } from "react-i18next"
import { useShelf } from "@/api/hooks/useShelf"
import FullListPage from "@/components/media/FullListPage"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"
import { usePlayShelfItem } from "@/hooks/usePlayShelfItem"
import type { ShelfItem } from "@/api/types"

/** `/shelf/:key` — the full list of a dashboard shelf. */
export default function ShelfPage() {
  const { t, i18n } = useTranslation("media")
  const { key = "" } = useParams<{ key: string }>()
  const playShelfItem = usePlayShelfItem()
  const source = useShelf(key)

  const firstPage = source.data?.pages[0]
  const title = t(`shelves.${key}`, { ns: "dashboard", defaultValue: firstPage?.title ?? key })

  return (
    <FullListPage<ShelfItem>
      title={title}
      source={source}
      getKey={(item) => `${item.entity_type}-${item.entity_id}`}
      toCard={(item) => shelfItemToCard(item, playShelfItem, t, i18n.language)}
    />
  )
}
