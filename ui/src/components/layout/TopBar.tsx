import { PanelLeftClose, PanelLeftOpen } from "lucide-react"
import { cn } from "@/lib/utils"
import { useUIStore } from "@/store/uiStore"
import ProfileButton from "@/components/profile/ProfileButton"
import { Button } from "@/components/ui/button"

export default function TopBar() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)

  return (
    <header className="h-14 shrink-0 flex items-center justify-between px-4 border-b border-border bg-background/80 backdrop-blur-sm z-10">
      {/* Left: logo + collapse toggle */}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon-sm" onClick={toggleSidebar} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
        </Button>
        <span className="text-primary font-bold text-lg tracking-tight select-none">discocs</span>
      </div>

      {/* Right: profile */}
      <ProfileButton />
    </header>
  )
}
