import { describe, expect, it } from "vitest"
import { freeDeck, initialDeckRoleState, reduceDeckRoles } from "./deckRoles"

describe("deck role reducer", () => {
  it("does not change program identity while the incoming deck prepares", () => {
    const initial = initialDeckRoleState()
    const loading = reduceDeckRoles(initial, { type: "preparing", deck: "B" })
    const ready = reduceDeckRoles(loading, { type: "prepared", deck: "B" })

    expect(ready.program).toBe("A")
    expect(ready.roles.B).toBe("prepared")
    expect(ready.preparation.B).toBe("ready")
  })

  it("alternates physical roles and frees only the retired outgoing deck", () => {
    let state = initialDeckRoleState()
    state = reduceDeckRoles(state, { type: "preparing", deck: "B" })
    state = reduceDeckRoles(state, { type: "prepared", deck: "B" })
    state = reduceDeckRoles(state, { type: "handover", incoming: "B" })

    expect(state.program).toBe("B")
    expect(state.roles).toEqual({ A: "outgoing", B: "program" })
    state = reduceDeckRoles(state, { type: "retire", deck: "A" })
    expect(freeDeck(state)).toBe("A")
    expect(state.roles.A).toBe("free")
  })

  it("ignores handover until incoming is ready", () => {
    const initial = initialDeckRoleState()
    expect(reduceDeckRoles(initial, { type: "handover", incoming: "B" })).toBe(initial)
  })
})
