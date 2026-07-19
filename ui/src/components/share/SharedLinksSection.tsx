import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, Loader2, Trash2 } from "lucide-react"
import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { listShares, revokeShare, useShareCapabilities } from "@/api/shares"

export default function SharedLinksSection() {
  const { t, i18n } = useTranslation("share")
  const queryClient = useQueryClient()
  const { data: capabilities } = useShareCapabilities()
  const shares = useQuery({
    queryKey: ["shares"],
    queryFn: listShares,
    enabled: capabilities?.can_create === true,
  })
  const revoke = useMutation({
    mutationFn: revokeShare,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["shares"] }),
  })

  if (!capabilities?.can_create) return null

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">{t("management.title")}</h2>
        <p className="mt-0.5 text-sm text-muted-foreground">{t("management.description")}</p>
      </div>
      {shares.isLoading ? (
        <Loader2 size={16} className="animate-spin text-muted-foreground" />
      ) : shares.data?.items.length ? (
        <div className="space-y-2">
          {shares.data.items.map((share) => (
            <div key={share.id} className="flex items-start gap-3 rounded-md bg-muted px-4 py-3">
              <ExternalLink size={16} className="mt-0.5 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{share.title || share.source_label}</p>
                <p className="text-xs text-muted-foreground">
                  {t(`management.status.${share.status}`)} · {t("management.items", { count: share.item_count })}
                </p>
                <p className="text-xs text-muted-foreground">
                  {share.expires_at
                    ? t("management.expires", { date: new Date(share.expires_at).toLocaleString(i18n.language) })
                    : t("management.neverExpires")}
                </p>
                {share.last_accessed_at && <p className="text-xs text-muted-foreground">{t("management.opens", { count: share.access_count })}</p>}
              </div>
              {share.status !== "revoked" && (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  disabled={revoke.isPending}
                  aria-label={t("management.revoke")}
                  title={t("management.revoke")}
                  onClick={() => {
                    if (window.confirm(t("management.revokeConfirm"))) revoke.mutate(share.id)
                  }}
                >
                  <Trash2 size={14} />
                </Button>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{t("management.empty")}</p>
      )}
    </section>
  )
}
