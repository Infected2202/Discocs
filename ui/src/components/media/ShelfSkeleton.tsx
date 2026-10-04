import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

interface ShelfSkeletonProps {
  readonly rows?: number
  /** Round artwork placeholders (user/artist cards). */
  readonly round?: boolean
}

/** Loading placeholder for dashboard shelves: a title bar plus a row of cards per shelf. */
export default function ShelfSkeleton({ rows = 1, round = false }: ShelfSkeletonProps) {
  return (
    <>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="space-y-3" data-testid="shelf-skeleton">
          <div className="px-4 sm:px-6 flex gap-3 items-baseline">
            <Skeleton className="h-5 w-36" />
            <Skeleton className="h-4 w-24" />
          </div>
          <div className="flex gap-1 px-3">
            {[1, 2, 3, 4, 5].map((j) => (
              <div key={j} className="w-44 shrink-0 p-3 space-y-3">
                <Skeleton className={cn("w-full aspect-square", round ? "rounded-full" : "rounded-md")} />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  )
}
