import type { DeckId, DeckRole, PreparationState } from "./types"

export interface DeckRoleState {
  program: DeckId
  roles: Record<DeckId, DeckRole>
  preparation: Record<DeckId, PreparationState>
}

export type DeckRoleAction =
  | { type: "preparing"; deck: DeckId }
  | { type: "prepared"; deck: DeckId }
  | { type: "unavailable"; deck: DeckId }
  | { type: "cancel-preparation"; deck: DeckId }
  | { type: "handover"; incoming: DeckId }
  | { type: "retire"; deck: DeckId }

export function initialDeckRoleState(): DeckRoleState {
  return {
    program: "A",
    roles: { A: "program", B: "free" },
    preparation: { A: "ready", B: "empty" },
  }
}

export function reduceDeckRoles(state: DeckRoleState, action: DeckRoleAction): DeckRoleState {
  if (action.type === "preparing") {
    return updateDeck(state, action.deck, "incoming", "loading")
  }
  if (action.type === "prepared") {
    return updateDeck(state, action.deck, "prepared", "ready")
  }
  if (action.type === "unavailable") {
    return updateDeck(state, action.deck, "free", "unavailable")
  }
  if (action.type === "cancel-preparation" || action.type === "retire") {
    return updateDeck(state, action.deck, "free", "empty")
  }
  if (action.incoming === state.program || state.preparation[action.incoming] !== "ready") return state
  const outgoing = state.program
  return {
    program: action.incoming,
    roles: { ...state.roles, [outgoing]: "outgoing", [action.incoming]: "program" },
    preparation: { ...state.preparation, [action.incoming]: "ready" },
  }
}

export function freeDeck(state: DeckRoleState): DeckId {
  return state.program === "A" ? "B" : "A"
}

function updateDeck(
  state: DeckRoleState,
  deck: DeckId,
  role: DeckRole,
  preparation: PreparationState,
): DeckRoleState {
  if (deck === state.program) return state
  return {
    ...state,
    roles: { ...state.roles, [deck]: role },
    preparation: { ...state.preparation, [deck]: preparation },
  }
}
