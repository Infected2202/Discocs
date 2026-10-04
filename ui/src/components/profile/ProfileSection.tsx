import type { ReactNode } from "react"
import { Link } from "react-router"

interface ProfileSectionProps {
  readonly title: string
  /** Optional "All" link on the right of the header. */
  readonly moreHref?: string
  readonly moreLabel?: string
  readonly children: ReactNode
}

/**
 * A titled block of the profile page whose body is not a Shelf (track lists,
 * stats). The header copies Shelf's: bold title, accent rule, small link.
 */
export default function ProfileSection({ title, moreHref, moreLabel, children }: ProfileSectionProps) {
  return (
    <section className="space-y-2" aria-label={title}>
      <div className="px-4 sm:px-6 flex items-center gap-2 min-w-0">
        <h2 className="text-sm font-semibold shrink-0">{title}</h2>
        <div aria-hidden="true" className="h-px min-w-3 flex-1 bg-primary/50" />
        {moreHref && (
          <Link
            to={moreHref}
            className="shrink-0 text-xs text-muted-foreground hover:text-foreground transition-colors px-1.5 py-0.5 rounded hover:bg-muted"
          >
            {moreLabel}
          </Link>
        )}
      </div>
      {children}
    </section>
  )
}
