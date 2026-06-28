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

  return (
    <aside
      className={cn(
        "shrink-0 flex flex-col bg-sidebar border-r border-sidebar-border h-full pb-[92px] transition-[width] duration-200",
        collapsed ? "w-16" : "w-[220px]",
      )}
    >
      {/* Nav */}
      <nav className="flex-1 px-2 py-3 space-y-1">
        <TooltipProvider delayDuration={200}>
          {NAV.map(({ to, label, icon: Icon, end }) => {
            const active = isactive(to, end)
            const cls = cn(
              "flex items-center rounded-md text-sm font-medium transition-colors",
              collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
              active
                ? "bg-sidebar-accent text-foreground"
                : "text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent/50",
            )

            if (collapsed) {
              return (
                <Tooltip key={to}>
                  <TooltipTrigger asChild>
                    <NavLink to={to} end={end} className={cls}>
                      <Icon size={18} strokeWidth={1.75} className="shrink-0" />
                    </NavLink>
                  </TooltipTrigger>
                  <TooltipContent side="right">{label}</TooltipContent>
                </Tooltip>
              )
            }

            return (
              <NavLink key={to} to={to} end={end} className={cls}>
                <Icon size={18} strokeWidth={1.75} className="shrink-0" />
                <span>{label}</span>
              </NavLink>
            )
          })}
        </TooltipProvider>
      </nav>

      {/* Collapse toggle */}
      <div className="px-2 py-2 border-t border-sidebar-border">
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={toggleSidebar}
                className={cn(
                  "flex items-center rounded-md text-sm font-medium transition-colors text-sidebar-foreground/70 hover:text-sidebar-foreground hover:bg-sidebar-accent/50 w-full",
                  collapsed ? "justify-center p-2.5" : "gap-3 px-3 py-2",
                )}
              >
                {collapsed ? <PanelLeftOpen size={18} strokeWidth={1.75} /> : <PanelLeftClose size={18} strokeWidth={1.75} />}
                {!collapsed && <span>Collapse</span>}
              </button>
            </TooltipTrigger>
            {collapsed && (
              <TooltipContent side="right">Expand sidebar</TooltipContent>
            )}
          </Tooltip>
        </TooltipProvider>
      </div>
    </aside>
  )
}
