import { createBrowserRouter } from "react-router"
import AppShell from "@/components/layout/AppShell"
import RequireAuth from "@/components/auth/RequireAuth"
import LoginPage from "@/pages/LoginPage"
import DashboardPage from "@/pages/DashboardPage"
import SearchPage from "@/pages/SearchPage"
import ArtistPage from "@/pages/ArtistPage"
import ReleasePage from "@/pages/ReleasePage"
import LabelPage from "@/pages/LabelPage"
import MixPage from "@/pages/MixPage"
import SettingsPage from "@/pages/SettingsPage"
import ShelfPage from "@/pages/ShelfPage"
import PlaylistPage from "@/pages/PlaylistPage"
import SharedPlayerPage from "@/pages/SharedPlayerPage"
import SharedLinksPage from "@/pages/SharedLinksPage"
import ProfilePage from "@/pages/ProfilePage"
import ListeningHistoryPage from "@/pages/ListeningHistoryPage"
import ArtistSimilarPage from "@/pages/ArtistSimilarPage"
import { ReleaseRecommendationsPage, ReleaseRelatedPage } from "@/pages/ReleaseListPages"
import { ProfileLikesPage, ProfilePlaylistsPage, ProfileTopPage } from "@/pages/ProfileListPages"

export const router = createBrowserRouter([
  { path: "/login", Component: LoginPage },
  { path: "/share/:token", Component: SharedPlayerPage },
  {
    Component: RequireAuth,
    children: [
      {
        Component: AppShell,
        children: [
          { index: true, Component: DashboardPage },
          { path: "search", Component: SearchPage },
          { path: "artists/:id", Component: ArtistPage },
          { path: "artists/:id/similar", Component: ArtistSimilarPage },
          { path: "releases/:id", Component: ReleasePage },
          { path: "releases/:id/related", Component: ReleaseRelatedPage },
          { path: "releases/:id/recommendations", Component: ReleaseRecommendationsPage },
          { path: "labels/:id", Component: LabelPage },
          { path: "mixes/:id", Component: MixPage },
          { path: "settings", Component: SettingsPage },
          { path: "shelf/:key", Component: ShelfPage },
          { path: "playlists/:id", Component: PlaylistPage },
          { path: "shared-links", Component: SharedLinksPage },
          { path: "u/:username", Component: ProfilePage },
          { path: "u/:username/history", Component: ListeningHistoryPage },
          { path: "u/:username/top/:kind", Component: ProfileTopPage },
          { path: "u/:username/likes/:kind", Component: ProfileLikesPage },
          { path: "u/:username/playlists", Component: ProfilePlaylistsPage },
        ],
      },
    ],
  },
])
