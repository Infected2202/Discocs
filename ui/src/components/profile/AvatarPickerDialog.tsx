import { useTranslation } from "react-i18next"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useSetMyAvatar } from "@/api/hooks/useProfile"
import { AVATAR_KEYS, avatarUrl } from "@/lib/avatars"
import { cn } from "@/lib/utils"

interface AvatarPickerDialogProps {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  /** The avatar key the user has now — marked in the grid. */
  readonly current: string
}

/** Grid of the built-in avatars; a click saves the choice right away and closes. */
export default function AvatarPickerDialog({ open, onOpenChange, current }: AvatarPickerDialogProps) {
  const { t } = useTranslation("user")
  const setAvatar = useSetMyAvatar()

  function pick(key: string) {
    if (key === current) {
      onOpenChange(false)
      return
    }
    setAvatar.mutate(key, { onSuccess: () => onOpenChange(false) })
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setAvatar.reset()
        onOpenChange(next)
      }}
    >
      {/* The dialog is centred and never scrolls itself, so with all the avatars
          it was taller than the screen and its top/bottom went off it. Cap the
          height and scroll only the grid: the header and the close button stay. */}
      <DialogContent
        aria-describedby={undefined}
        className="max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)]"
      >
        <DialogHeader>
          <DialogTitle>{t("chooseAvatar")}</DialogTitle>
        </DialogHeader>
        {/* p-1/-m-1: room for the selection ring (ring + offset ≈ 4px), which overflow would clip */}
        <div data-testid="avatar-picker-scroll" className="-m-1 min-h-0 space-y-3 overflow-y-auto p-1">
          <div className="grid grid-cols-3 gap-3">
            {AVATAR_KEYS.map((key, index) => {
              const selected = key === current
              return (
                <button
                  key={key}
                  type="button"
                  aria-label={t("avatarOption", { index: index + 1 })}
                  aria-pressed={selected}
                  disabled={setAvatar.isPending}
                  onClick={() => pick(key)}
                  className={cn(
                    "aspect-square overflow-hidden rounded-full ring-2 ring-offset-2 ring-offset-popover transition",
                    selected ? "ring-primary" : "ring-transparent hover:ring-muted-foreground/40",
                    "disabled:opacity-60",
                  )}
                >
                  <img src={avatarUrl(key) ?? undefined} alt="" className="h-full w-full object-cover" />
                </button>
              )
            })}
          </div>
          {setAvatar.isError && (
            <p role="alert" className="text-sm text-destructive">{t("avatarSaveError")}</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
