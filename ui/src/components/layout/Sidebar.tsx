import { NavLink, useLocation } from "react-router"
import { Home, Search, PanelLeftClose, PanelLeftOpen } from "lucide-react"
import { cn } from "@/lib/utils"
import { useUIStore } from "@/store/uiStore"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"

const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/search", label: "Search", icon: Search, end: false },
]

export default function Sidebar() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const location = useLocation()

  function isactive(to: string, end: boolean) {
    return end ? location.pathname === to : location.pathname.startsWith(to)
  }

  const textCls = cn(
    "overflow-hidden whitespace-nowrap transition-[opacity,max-width] duration-200",
    collapsed ? "opacity-0 max-w-0" : "opacity-100 max-w-[160px]",
  )

  const itemCls = "flex items-center gap-3 px-2.5 py-2 rounded-md text-sm font-medium transition-colors"

  return (
    <aside
      className={cn(
        "shrink-0 flex flex-col bg-background/50 backdrop-blur-sm border-r border-border h-full pb-[92px] transition-[width] duration-200 cursor-pointer",
        collapsed ? "w-14" : "w-[220px]",
      )}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("button, a")) return
        toggleSidebar()
      }}
    >
      {/* Collapse toggle — top */}
      <div className="px-2 pt-3 pb-1 shrink-0">
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={toggleSidebar}
                className={cn(itemCls, "w-full text-muted-foreground hover:text-foreground hover:bg-muted/60")}
              >
                {/* Cross-fade between the two icons so there's no flash */}
                <span className="relative shrink-0 w-[18px] h-[18px]">
                  <PanelLeftClose
                    size={18}
                    strokeWidth={1.75}
                    className={cn("absolute inset-0 transition-opacity duration-200", collapsed ? "opacity-0" : "opacity-100")}
                  />
                  <PanelLeftOpen
                    size={18}
                    strokeWidth={1.75}
                    className={cn("absolute inset-0 transition-opacity duration-200", collapsed ? "opacity-100" : "opacity-0")}
                  />
                </span>
                <span className={textCls}>Collapse</span>
              </button>
            </TooltipTrigger>
            {collapsed && (
              <TooltipContent side="right">Expand sidebar</TooltipContent>
            )}
          </Tooltip>
        </TooltipProvider>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-1 space-y-1">
        <TooltipProvider delayDuration={200}>
          {NAV.map(({ to, label, icon: Icon, end }) => {
            const active = isactive(to, end)
            const cls = cn(
              itemCls,
              active
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/60",
            )

            const link = (
              <NavLink key={to} to={to} end={end} className={cls}>
                <Icon size={18} strokeWidth={1.75} className="shrink-0" />
                <span className={textCls}>{label}</span>
              </NavLink>
            )

            if (collapsed) {
              return (
                <Tooltip key={to}>
                  <TooltipTrigger asChild>{link}</TooltipTrigger>
                  <TooltipContent side="right">{label}</TooltipContent>
                </Tooltip>
              )
            }

            return link
          })}
        </TooltipProvider>
      </nav>
    </aside>
  )
}
