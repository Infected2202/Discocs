import { useNavigate } from "react-router"
import { useQuery } from "@tanstack/react-query"
import { Settings, User, LogOut } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavidromeStatus } from "@/api/hooks/useNavidromeStatus"
import { Button } from "@/components/ui/button"
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover"
import { getSession, logout } from "@/api/auth"
import { redirectToLogin } from "@/lib/authRedirect"

function navidromeStatusUi(status: string, isLoading: boolean) {
  if (isLoading) {
    return {
      dotColor: "bg-muted-foreground",
      statusLabel: "Checking…",
    }
  }
  if (status === "connected") {
    return {
      dotColor: "bg-green-500",
      statusLabel: "Navidrome authenticated",
    }
  }
  return {
    dotColor: "bg-red-500",
    statusLabel: "Navidrome not connected",
  }
}

export default function ProfileButton({ mobile = false }: { readonly mobile?: boolean }) {
  const navigate = useNavigate()
  const { status, isLoading } = useNavidromeStatus()
  const { data: session } = useQuery({
    queryKey: ["auth", "session"],
    queryFn: getSession,
    retry: false,
    staleTime: 60_000,
  })

  async function handleLogout() {
    try {
      await logout()
    } finally {
      redirectToLogin()
    }
  }

  const { dotColor, statusLabel } = navidromeStatusUi(status, isLoading)
  const logoutLabel = session?.username ? `Sign out (${session.username})` : "Sign out"

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="relative flex h-8 w-8 items-center justify-center rounded-full transition-colors hover:bg-muted/40"
          title={session?.username ? `Profile: ${session.username}` : "Profile"}
        >
          <User size={16} />
          <span className={cn("absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-background", dotColor)} />
        </button>
      </PopoverTrigger>
      <PopoverContent align={mobile ? "center" : "end"} className="w-56 p-3">
        <div className="space-y-3">
          <div className="flex items-center gap-2 border-b border-border/50 pb-3">
            <User size={16} className="text-muted-foreground" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{session?.username ?? "Unknown user"}</p>
              <p className="text-xs text-muted-foreground">Signed in</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", dotColor)} />
            <span className="text-sm">{statusLabel}</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => navigate("/settings")}
          >
            <Settings size={14} className="mr-2" />
            Open settings
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="w-full text-muted-foreground"
            onClick={handleLogout}
          >
            <LogOut size={14} className="mr-2" />
            {logoutLabel}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
