import { Link } from "react-router"
import type { TrackCredit } from "@/api/types"
import { splitTitleByCredits } from "@/lib/titleCredits"
import { cn } from "@/lib/utils"

interface TrackTitleProps {
  readonly title: string
  readonly credits?: readonly TrackCredit[]
  /** Where the rest of the title leads (the release); none → plain text. */
  readonly href?: string
  readonly className?: string
  /** Classes of the links over the plain parts of the title. */
  readonly linkClassName?: string
  /** Navigate with history replace (the expanded player closes over the page). */
  readonly replace?: boolean
}

/** Track title whose featured artists and remixers ("(ft. X)", "(X Remix)")
 *  are links to their artist pages right where the title names them. */
export default function TrackTitle({ title, credits, href, className, linkClassName, replace }: TrackTitleProps) {
  const stop = (event: React.MouseEvent) => event.stopPropagation()
  return (
    <span className={className}>
      {splitTitleByCredits(title, credits).map((segment, index) => {
        const key = `${index}:${segment.text}`
        if (segment.credit) {
          return (
            <Link
              key={key}
              to={`/artists/${segment.credit.id}`}
              replace={replace}
              onClick={stop}
              className="underline decoration-current/30 decoration-dotted underline-offset-2 hover:decoration-current hover:decoration-solid"
            >
              {segment.text}
            </Link>
          )
        }
        if (href) {
          return (
            <Link key={key} to={href} replace={replace} onClick={stop} className={cn(linkClassName)}>
              {segment.text}
            </Link>
          )
        }
        return <span key={key}>{segment.text}</span>
      })}
    </span>
  )
}
