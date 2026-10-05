import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import TelegramLinkSection from "./TelegramLinkSection"

const startTelegramLink = vi.fn()
const unlinkTelegram = vi.fn()
const linkQuery = {
  data: undefined as unknown,
  isLoading: false,
  isError: false,
}

vi.mock("@/api/telegram", () => ({
  TELEGRAM_LINK_QUERY_KEY: ["telegram-link"],
  startTelegramLink: (...args: unknown[]) => startTelegramLink(...args),
  unlinkTelegram: (...args: unknown[]) => unlinkTelegram(...args),
  useTelegramLink: () => linkQuery,
}))

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <TelegramLinkSection />
    </QueryClientProvider>,
  )
}

describe("TelegramLinkSection", () => {
  beforeEach(() => {
    startTelegramLink.mockReset()
    unlinkTelegram.mockReset()
    linkQuery.data = undefined
    linkQuery.isError = false
  })

  it("stays hidden while the bot integration is not configured", () => {
    linkQuery.data = { enabled: false, linked: false, link: null }
    renderSection()

    expect(screen.queryByRole("heading", { name: "Telegram" })).not.toBeInTheDocument()
  })

  it("turns Connect into a deep link the user opens themselves", async () => {
    linkQuery.data = { enabled: true, linked: false, link: null }
    startTelegramLink.mockResolvedValueOnce({
      url: "https://t.me/discocs_bot?start=link_abc",
      expires_at: "2026-10-05T12:10:00+00:00",
    })
    renderSection()

    fireEvent.click(screen.getByRole("button", { name: "Connect Telegram" }))

    const link = await screen.findByRole("link", { name: "Open the bot in Telegram" })
    expect(link).toHaveAttribute("href", "https://t.me/discocs_bot?start=link_abc")
    expect(link).toHaveAttribute("target", "_blank")
    expect(startTelegramLink).toHaveBeenCalledTimes(1)
  })

  it("shows the linked account and unlinks it", async () => {
    linkQuery.data = {
      enabled: true,
      linked: true,
      link: { telegram_username: "alice_tg", linked_at: "2026-10-05T12:00:00+00:00" },
    }
    unlinkTelegram.mockResolvedValueOnce(undefined)
    renderSection()

    expect(screen.getByText("Connected as @alice_tg")).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Unlink" }))

    await waitFor(() => expect(unlinkTelegram).toHaveBeenCalledTimes(1))
    expect(startTelegramLink).not.toHaveBeenCalled()
  })

  it("reports a bot that cannot be reached", async () => {
    linkQuery.data = { enabled: true, linked: false, link: null }
    startTelegramLink.mockRejectedValueOnce(new Error("HTTP 503"))
    renderSection()

    fireEvent.click(screen.getByRole("button", { name: "Connect Telegram" }))

    expect(await screen.findByText("Could not reach the bot. Try again later.")).toBeInTheDocument()
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
  })
})
