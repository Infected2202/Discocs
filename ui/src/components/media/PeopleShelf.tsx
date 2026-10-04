import { useTranslation } from "react-i18next"
import Shelf from "./Shelf"
import ShelfSkeleton from "./ShelfSkeleton"
import { usePeople } from "@/api/hooks/usePeople"
import { avatarUrl } from "@/lib/avatars"
import type { Person } from "@/api/social"
import type { MediaCardProps } from "./MediaCard"

/** Person → user card: avatar, login, and «Track — Artist» with a live dot only while playing. */
export function personToCard(person: Person): MediaCardProps {
  const playing = person.now_playing
  const url = avatarUrl(person.avatar)
  return {
    id: person.username,
    type: "user",
    title: person.username,
    subtitle: playing ? [playing.title, playing.artists].filter(Boolean).join(" — ") : null,
    live: playing !== null,
    artwork: { url, source: url ? "local" : "none", placeholder: url === null },
  }
}

/**
 * «Люди» — the first dashboard shelf (docs/social.md). Order is the API's
 * (playing first). Hidden when there is nobody else or the first request
 * failed; a later failed poll keeps the last list on screen.
 */
export default function PeopleShelf() {
  const { t } = useTranslation("dashboard")
  const { data, isPending } = usePeople()

  if (isPending) return <ShelfSkeleton round />

  const people = data?.items ?? []
  if (people.length === 0) return null

  return <Shelf title={t("shelves.people")} items={people.map(personToCard)} />
}
