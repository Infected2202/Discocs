import { useNavigate } from "react-router"
import { Settings, CircleDot } from "lucide-react"
import { cn } from "@/lib/utils"
import { useNavidromeStatus } from "@/api/hooks/useNavidromeStatus"
import { Button } from "@/components/ui/button"
import {
  Popover, PopoverContent, PopoverTrigger,
} from "@/components/ui/popover"

export default function ProfileButton({ mobile = false }: { mobile?: boolean }) {
  const navigate = useNavigate()
  const { status, isLoading } = useNavidromeStatus()

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
          className="p-1 rounded-full hover:bg-muted/40 transition-colors"
          title="Profile"
        >
          <CircleDot size={22} className={dotColor} />
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
        </div>
      </PopoverContent>
    </Popover>
  )
}
