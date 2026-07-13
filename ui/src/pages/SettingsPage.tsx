import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { useTranslation } from "react-i18next"
import { CheckCircle2, XCircle, Loader2, Radio, Activity } from "lucide-react"
import { apiFetch, apiUrl } from "@/api/client"
import { Button } from "@/components/ui/button"

// ---------------------------------------------------------------------------
// Flow Profile section
// ---------------------------------------------------------------------------

interface FlowProfileStatus {
  model_key: string
  status: string
  region_count: number
  last_built_at?: string | null
}

function FlowProfileSection() {
  const { t, i18n } = useTranslation("settings")
  const qc = useQueryClient()

  const { data: status, isLoading: loadingStatus } = useQuery<FlowProfileStatus>({
    queryKey: ["flow-profile-status"],
    queryFn: () => apiFetch(apiUrl("/api/v1/jobs/flow-profile/status")),
    refetchInterval: (q) =>
      q.state.data?.status === "building" ? 2000 : false,
  })

  const { mutate: rebuild, isPending: building } = useMutation({
    mutationFn: () =>
      apiFetch(apiUrl("/api/v1/jobs/flow-profile"), { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["flow-profile-status"] })
      qc.invalidateQueries({ queryKey: ["flow-profile"] })
    },
  })

  const isBuilding = building || status?.status === "building"
  const isBuilt = status?.status === "ready" || status?.status === "cold_start"

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">{t("flowProfile.heading")}</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          {t("flowProfile.description")}
        </p>
      </div>

      {loadingStatus ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 size={14} className="animate-spin" />
          {t("status.loading", { ns: "common" })}
        </div>
      ) : (
        <div className="space-y-4">
          {/* Status card */}
          <div className="rounded-md bg-muted px-4 py-3 space-y-1.5">
            <div className="flex items-center gap-2 text-sm font-medium">
              {status?.status === "ready" ? (
                <CheckCircle2 size={14} className="text-green-500" />
              ) : status?.status === "cold_start" ? (
                <Radio size={14} className="text-blue-500" />
              ) : status?.status === "building" ? (
                <Loader2 size={14} className="animate-spin text-muted-foreground" />
              ) : status?.status === "empty" ? (
                <XCircle size={14} className="text-yellow-500" />
              ) : (
                <Activity size={14} className="text-muted-foreground" />
              )}
              <span className={
                status?.status === "ready" ? "text-green-500"
                  : status?.status === "cold_start" ? "text-blue-500"
                    : "text-foreground"
              }>
                {t(`flowProfile.status.${status?.status ?? "not_built"}`, {
                  defaultValue: status?.status ?? t("status.unknown", { ns: "common" }),
                })}
              </span>
            </div>

            {status && status.status !== "not_built" && (
              <div className="text-xs text-muted-foreground space-y-0.5">
                {status.region_count > 0 && (
                  <p>{t("flowProfile.regionCount", { count: status.region_count })}</p>
                )}
                {status.last_built_at && (
                  <p>{t("flowProfile.lastBuilt", {
                    date: new Date(status.last_built_at).toLocaleString(i18n.language),
                  })}</p>
                )}
              </div>
            )}
          </div>

          <Button
            size="sm"
            variant={isBuilt ? "outline" : "default"}
            disabled={isBuilding}
            onClick={() => rebuild()}
            className="gap-2"
          >
            {isBuilding
              ? <><Loader2 size={13} className="animate-spin" />{t("flowProfile.buildingButton")}</>
              : isBuilt
                ? t("flowProfile.rebuildButton")
                : t("flowProfile.buildButton")}
          </Button>

          {status?.status === "cold_start" && (
            <p className="text-xs text-muted-foreground">
              {t("flowProfile.coldStartHint")}
            </p>
          )}

          {status?.status === "empty" && (
            <p className="text-xs text-muted-foreground">
              {t("flowProfile.emptyHint")}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { t } = useTranslation("settings")
  return (
    <div className="py-8 px-4 sm:px-6 max-w-lg space-y-10">
      <div>
        <h1 className="text-2xl font-bold">{t("page.heading")}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t("page.description")}
        </p>
      </div>

      <FlowProfileSection />
    </div>
  )
}
