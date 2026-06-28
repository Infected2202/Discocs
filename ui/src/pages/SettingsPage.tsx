import { useState, useEffect } from "react"
import { useQuery, useMutation } from "@tanstack/react-query"
import { CheckCircle2, XCircle, Loader2, Radio } from "lucide-react"
import { fetchNavidromeSettings, saveNavidromeSettings, pingNavidrome } from "@/api/settings"
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
    <div className="py-8 px-6 max-w-lg space-y-10">
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
    </div>
  )
}
