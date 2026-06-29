import { useState, useEffect } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, XCircle, Loader2, Radio, Activity } from "lucide-react"
import { fetchNavidromeSettings, saveNavidromeSettings, pingNavidrome } from "@/api/settings"
import { apiFetch, apiUrl } from "@/api/client"
import { Button } from "@/components/ui/button"

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

function Input({
  value,
  onChange,
  type = "text",
  placeholder,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  disabled?: boolean
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full rounded-md bg-muted px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
    />
  )
}

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

  const statusLabel: Record<string, string> = {
    not_built: "Not built",
    building: "Building…",
    ready: "Ready",
    cold_start: "Exploring (cold start)",
    empty: "No eligible tracks",
  }

  const isBuilding = building || status?.status === "building"
  const isBuilt = status?.status === "ready" || status?.status === "cold_start"

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-base font-semibold">Flow Profile</h2>
        <p className="text-sm text-muted-foreground mt-0.5">
          Flow uses your listening history to build a personal taste profile.
          Rebuild after you've added new music or want to refresh recommendations.
        </p>
      </div>

      {loadingStatus ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 size={14} className="animate-spin" />
          Loading…
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
                {statusLabel[status?.status ?? "not_built"] ?? status?.status ?? "Unknown"}
              </span>
            </div>

            {status && status.status !== "not_built" && (
              <div className="text-xs text-muted-foreground space-y-0.5">
                {status.region_count > 0 && (
                  <p>{status.region_count} taste region{status.region_count !== 1 ? "s" : ""}</p>
                )}
                {status.last_built_at && (
                  <p>Last built {new Date(status.last_built_at).toLocaleString()}</p>
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
              ? <><Loader2 size={13} className="animate-spin" />Building…</>
              : isBuilt
                ? "Rebuild Profile"
                : "Build Profile"}
          </Button>

          {status?.status === "cold_start" && (
            <p className="text-xs text-muted-foreground">
              No taste signal yet — Flow is exploring a diverse sample of your
              library. Like tracks and rebuild to personalise.
            </p>
          )}

          {status?.status === "empty" && (
            <p className="text-xs text-muted-foreground">
              No tracks with embeddings found. Analyze your library and try again.
            </p>
          )}
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { data: saved, isLoading } = useQuery({
    queryKey: ["navidrome-settings"],
    queryFn: fetchNavidromeSettings,
    retry: false,
  })

  const [url, setUrl]   = useState("")
  const [user, setUser] = useState("")
  const [pass, setPass] = useState("")

  useEffect(() => {
    if (saved) {
      setUrl(saved.url ?? "")
      setUser(saved.user ?? "")
    }
  }, [saved])

  const { mutate: save, isPending: saving, isSuccess: saved_, isError: saveError } = useMutation({
    mutationFn: () =>
      saveNavidromeSettings({
        url,
        user,
        ...(pass ? { password: pass } : {}),
      }),
    onSuccess: () => setPass(""),
  })

  const {
    mutate: ping,
    isPending: pinging,
    data: pingResult,
    isError: pingError,
    reset: resetPing,
  } = useMutation({ mutationFn: pingNavidrome })

  function handleSave(e: React.FormEvent) {
    e.preventDefault()
    resetPing()
    save()
  }

  return (
    <div className="py-8 px-4 sm:px-6 max-w-lg space-y-10">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground mt-1">Configure your discocs instance.</p>
      </div>

      {/* Navidrome connection */}
      <section className="space-y-5">
        <div>
          <h2 className="text-base font-semibold">Navidrome</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Connect to your Navidrome server for library sync and scrobbling.
          </p>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            Loading…
          </div>
        ) : (
          <form onSubmit={handleSave} className="space-y-4">
            <Field label="Server URL" hint="e.g. http://navidrome:4533">
              <Input
                value={url}
                onChange={setUrl}
                placeholder="http://localhost:4533"
                type="url"
              />
            </Field>

            <Field label="Username">
              <Input value={user} onChange={setUser} placeholder="admin" />
            </Field>

            <Field
              label="Password"
              hint={saved?.password_set ? "Password is set — leave blank to keep unchanged." : undefined}
            >
              <Input
                value={pass}
                onChange={setPass}
                type="password"
                placeholder={saved?.password_set ? "••••••••" : "Enter password"}
              />
            </Field>

            {/* Status */}
            {saved_ && (
              <div className="flex items-center gap-2 text-sm text-green-500">
                <CheckCircle2 size={14} />
                Saved.
              </div>
            )}
            {saveError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <XCircle size={14} />
                Failed to save.
              </div>
            )}

            <div className="flex gap-3 pt-1">
              <Button type="submit" disabled={saving} size="sm">
                {saving ? <><Loader2 size={13} className="animate-spin mr-1.5" />Saving…</> : "Save"}
              </Button>

              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pinging || !url || !user}
                onClick={() => { resetPing(); ping() }}
                className="gap-2"
              >
                {pinging
                  ? <><Loader2 size={13} className="animate-spin" />Testing…</>
                  : <><Radio size={13} />Test connection</>}
              </Button>
            </div>

            {/* Ping result */}
            {pingResult && (
              <div className="rounded-md bg-muted px-4 py-3 space-y-0.5">
                <div className="flex items-center gap-2 text-sm font-medium text-green-500">
                  <CheckCircle2 size={14} />
                  Connected
                </div>
                <p className="text-xs text-muted-foreground">
                  Navidrome {pingResult.server_version} · API {pingResult.version}
                </p>
              </div>
            )}
            {pingError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <XCircle size={14} />
                Connection failed — check URL and credentials.
              </div>
            )}
          </form>
        )}
      </section>

      {/* Flow Profile */}
      <FlowProfileSection />
    </div>
  )
}
