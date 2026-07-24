import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router/dom"
import { QueryClientProvider } from "@tanstack/react-query"

import "./index.css"
import "./i18n"
import { router } from "./router"
import { queryClient } from "./api/queryClient"
import { initNative } from "./lib/nativeInit"

void initNative()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>
)
