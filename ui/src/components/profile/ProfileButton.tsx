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

  const dotColor = isLoading
    ? "bg-muted-foreground"
    : status === "connected"
      ? "bg-green-500"
      : "bg-red-500"

  const statusLabel = isLoading
    ? "Checking…"
    : status === "connected"
      ? "Navidrome connected"
      : "Navidrome not connected"

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          className="relative w-8 h-8 rounded-full flex items-center justify-center hover:bg-muted/40 transition-colors"
          title="Profile"
        >
          <User size={16} />
          <span className={cn("absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full border-2 border-background", dotColor)} />
        </button>
      </PopoverTrigger>
      <PopoverContent align={mobile ? "center" : "end"} className="w-56 p-3">
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className={cn("w-2 h-2 rounded-full shrink-0", dotColor)} />
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
          {session?.enabled && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full text-muted-foreground"
              onClick={handleLogout}
            >
              <LogOut size={14} className="mr-2" />
              {session.username ? `Выйти (${session.username})` : "Выйти"}
            </Button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
