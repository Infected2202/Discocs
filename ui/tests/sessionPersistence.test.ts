import assert from "node:assert/strict"
import test from "node:test"

// Minimal localStorage mock
const _store: Record<string, string> = {}
const mockLocalStorage = {
  getItem: (k: string) => _store[k] ?? null,
  setItem: (k: string, v: string) => { _store[k] = v },
  removeItem: (k: string) => { delete _store[k] },
  clear: () => { for (const k of Object.keys(_store)) delete _store[k] },
  length: 0,
  key: () => null,
}
// @ts-ignore
global.localStorage = mockLocalStorage

// Import after global is set
const { persistSessionId, loadPersistedSessionId, clearPersistedSessionId } =
  await import("../src/store/sessionPersistence.ts")

test("persistSessionId stores the id", () => {
  mockLocalStorage.clear()
  persistSessionId("abc-123")
  assert.equal(loadPersistedSessionId(), "abc-123")
})

test("loadPersistedSessionId returns null when nothing stored", () => {
  mockLocalStorage.clear()
  assert.equal(loadPersistedSessionId(), null)
})

test("clearPersistedSessionId removes the stored id", () => {
  mockLocalStorage.clear()
  persistSessionId("xyz-999")
  clearPersistedSessionId()
  assert.equal(loadPersistedSessionId(), null)
})

test("overwriting persisted id keeps only the latest", () => {
  mockLocalStorage.clear()
  persistSessionId("first")
  persistSessionId("second")
  assert.equal(loadPersistedSessionId(), "second")
})
