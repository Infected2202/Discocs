import { createBrowserRouter } from "react-router"
import AppShell from "@/components/layout/AppShell"
import DashboardPage from "@/pages/DashboardPage"
import SearchPage from "@/pages/SearchPage"
import ArtistPage from "@/pages/ArtistPage"
import ReleasePage from "@/pages/ReleasePage"
import MixPage from "@/pages/MixPage"

export const router = createBrowserRouter([
  {
    Component: AppShell,
    children: [
      { index: true, Component: DashboardPage },
      { path: "search", Component: SearchPage },
      { path: "artists/:id", Component: ArtistPage },
      { path: "releases/:id", Component: ReleasePage },
      { path: "mixes/:id", Component: MixPage },
    ],
  },
])
