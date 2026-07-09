import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"
import VirtualTrackRow from "./VirtualTrackRow"
import type { TrackSummary } from "@/api/types"

vi.mock("./TrackMenu", () => ({
  default: () => <div data-testid="track-menu" />,
}))

const track = {
  id: 7,
  title: "Song",
  artists: [],
  release: null,
  duration: 120,
  artwork: { url: null, source: "placeholder", placeholder: true },
} as unknown as TrackSummary

function renderRow(props: Partial<React.ComponentProps<typeof VirtualTrackRow>> = {}) {
  return render(
    <MemoryRouter>
      <VirtualTrackRow track={track} index={0} {...props} />
    </MemoryRouter>
  )
}

describe("VirtualTrackRow — selection", () => {
  it("без selectable чекбокса нет даже в selection-режиме", () => {
    renderRow({ selectionActive: true })
    expect(screen.queryByRole("checkbox")).toBeNull()
  })

  it("selectable: чекбокс виден при активном выделении, клик зовёт onToggleSelect", () => {
    const onToggleSelect = vi.fn()
    renderRow({ selectable: true, selectionActive: true, onToggleSelect })

    const checkbox = screen.getByRole("checkbox", { name: "Select Song" })
    expect(checkbox).toHaveAttribute("aria-checked", "false")

    fireEvent.click(checkbox)
    expect(onToggleSelect).toHaveBeenCalledWith(7)
  })

  it("selectable без hover и без активного выделения — обычный порядковый номер", () => {
    renderRow({ selectable: true })
    expect(screen.queryByRole("checkbox")).toBeNull()
    expect(screen.getByText("1")).toBeInTheDocument()
  })

  it("выбранная строка помечена aria-checked", () => {
    renderRow({ selectable: true, selectionActive: true, selected: true })
    expect(screen.getByRole("checkbox")).toHaveAttribute("aria-checked", "true")
  })
})
