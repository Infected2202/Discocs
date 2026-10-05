import type { Namespace, TFunction } from "i18next"
import type { MediaCardProps } from "./MediaCard"
import type { LabelSummary } from "@/api/types"

/**
 * The one label card caption everywhere (dashboard, artist's labels, full lists):
 * the name and the label's release count — no styles.
 */
export function labelCardSubtitle<Ns extends Namespace>(releaseCount: number, t: TFunction<Ns>): string {
  return String(t("releaseCount", { ns: "label", count: releaseCount }))
}

/** Label summary → card; a label is not a playback source, so there is no Play button. */
export function labelToCard<Ns extends Namespace>(label: LabelSummary, t: TFunction<Ns>): MediaCardProps {
  return {
    id: label.id,
    type: "label",
    title: label.name,
    subtitle: labelCardSubtitle(label.release_count, t),
    artwork: label.artwork,
  }
}
