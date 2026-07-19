import { useState } from "react"
import { Check, Copy, Loader2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { createShare, type ShareSourceType } from "@/api/shares"

interface CreateShareDialogProps {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
  readonly sourceType: ShareSourceType
  readonly sourceId: number
  readonly sourceTitle: string
}

const TTL_OPTIONS = [24, 168, 720, 8760] as const

export default function CreateShareDialog({
  open,
  onOpenChange,
  sourceType,
  sourceId,
  sourceTitle,
}: CreateShareDialogProps) {
  const { t } = useTranslation("share")
  const [title, setTitle] = useState("")
  const [ttl, setTtl] = useState<string>("168")
  const [confirmNever, setConfirmNever] = useState(false)
  const [url, setUrl] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setTitle("")
    setTtl("168")
    setConfirmNever(false)
    setUrl(null)
    setPending(false)
    setCopied(false)
    setError(null)
  }

  async function submit() {
    if (ttl === "never" && !confirmNever) return
    setPending(true)
    setError(null)
    try {
      const expiresAt = ttl === "never"
        ? null
        : new Date(Date.now() + Number(ttl) * 3_600_000).toISOString()
      const result = await createShare({
        source_type: sourceType,
        source_id: sourceId,
        title: title.trim() || undefined,
        expires_at: expiresAt,
      })
      setUrl(result.url)
    } catch (err) {
      setError(err instanceof Error ? err.message : t("createError"))
    } finally {
      setPending(false)
    }
  }

  async function copy() {
    if (!url) return
    await navigator.clipboard.writeText(url)
    setCopied(true)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) reset()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("dialogTitle")}</DialogTitle>
          <DialogDescription>{t("dialogDescription", { title: sourceTitle })}</DialogDescription>
        </DialogHeader>

        {url ? (
          <div className="space-y-3">
            <label htmlFor="share-url" className="text-sm font-medium">{t("shareUrl")}</label>
            <div className="flex gap-2">
              <input id="share-url" readOnly value={url} className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm" />
              <Button type="button" variant="outline" size="icon" onClick={copy} aria-label={t("copy")}>
                {copied ? <Check size={16} /> : <Copy size={16} />}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t("urlShownOnce")}</p>
            <a href="/shared-links" className="inline-block text-xs text-primary hover:underline">{t("manageLinks")}</a>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="share-title" className="text-sm font-medium">{t("label")}</label>
              <input
                id="share-title"
                value={title}
                maxLength={200}
                onChange={(event) => setTitle(event.target.value)}
                placeholder={sourceTitle}
                className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="share-ttl" className="text-sm font-medium">{t("expires")}</label>
              <select
                id="share-ttl"
                value={ttl}
                onChange={(event) => {
                  setTtl(event.target.value)
                  setConfirmNever(false)
                }}
                className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              >
                {TTL_OPTIONS.map((hours) => <option key={hours} value={hours}>{t(`ttl.${hours}`)}</option>)}
                <option value="never">{t("ttl.never")}</option>
              </select>
            </div>
            {ttl === "never" && (
              <label className="flex items-start gap-2 rounded-md bg-muted p-3 text-xs">
                <input type="checkbox" checked={confirmNever} onChange={(event) => setConfirmNever(event.target.checked)} />
                <span>{t("confirmNever")}</span>
              </label>
            )}
            <p className="text-xs text-muted-foreground">{t("capabilityWarning")}</p>
            {error && <p className="text-xs text-destructive">{error}</p>}
          </div>
        )}

        <DialogFooter>
          {!url && (
            <Button onClick={submit} disabled={pending || (ttl === "never" && !confirmNever)}>
              {pending && <Loader2 size={14} className="animate-spin" />}
              {t("create")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
