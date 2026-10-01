import type { ReactNode } from "react"
import { useTranslation } from "react-i18next"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"

interface ConfirmDialogProps {
  readonly open: boolean
  readonly title: string
  readonly description?: ReactNode
  /** Label of the confirming (destructive) button. */
  readonly confirmLabel: string
  readonly pending?: boolean
  /** Shown under the description when the confirmed action failed. */
  readonly error?: string | null
  readonly onConfirm: () => void
  readonly onCancel: () => void
}

/**
 * In-app confirmation for destructive actions. Replaces the browser's native
 * confirm(): that one is styled by the browser, says nothing about *what* is
 * about to go away beyond a line of text, and lets Enter confirm instantly.
 * Focus starts on Cancel so a stray Enter never destroys anything.
 */
export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pending = false,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation("common")
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !pending) onCancel() }}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={onCancel} disabled={pending} autoFocus>
            {t("actions.cancel")}
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={pending}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
