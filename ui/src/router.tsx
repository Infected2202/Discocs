import { createBrowserRouter } from "react-router"

export const router = createBrowserRouter([
  {
    path: "/",
    element: <div className="p-8 text-foreground">Dashboard (coming in Phase 5)</div>,
  },
  {
    path: "/search",
    element: <div className="p-8 text-foreground">Search (coming in Phase 5)</div>,
  },
  {
    path: "/artists/:id",
    element: <div className="p-8 text-foreground">Artist (coming in Phase 5)</div>,
  },
  {
    path: "/releases/:id",
    element: <div className="p-8 text-foreground">Release (coming in Phase 5)</div>,
  },
  {
    path: "/mixes/:id",
    element: <div className="p-8 text-foreground">Mix (coming in Phase 5)</div>,
  },
])
