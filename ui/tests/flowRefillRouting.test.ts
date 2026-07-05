import assert from "node:assert/strict"
import { test } from "vitest"
import { planRefill } from "../src/store/flowRefillRouting.ts"

test("flow session with feedback event → flow engine + sendEvent", () => {
  for (const ev of ["completed", "skipped", "liked", "disliked"]) {
    const r = planRefill("flow", ev)
    assert.equal(r.engine, "flow", `engine for event=${ev}`)
    assert.equal(r.sendEvent, true, `sendEvent for event=${ev}`)
  }
})

test("flow session without event (on start/refill) → flow engine, no sendEvent", () => {
  const r = planRefill("flow", undefined)
  assert.equal(r.engine, "flow")
  assert.equal(r.sendEvent, false)
})

test("flow session with non-feedback event → flow engine, no sendEvent", () => {
  const r = planRefill("flow", "track_started")
  assert.equal(r.engine, "flow")
  assert.equal(r.sendEvent, false)
})

test("track session (instamix) → autoplay engine", () => {
  const r = planRefill("track", "completed")
  assert.equal(r.engine, "autoplay")
  assert.equal(r.sendEvent, false)
})

test("release session → autoplay engine", () => {
  const r = planRefill("release", "skipped")
  assert.equal(r.engine, "autoplay")
  assert.equal(r.sendEvent, false)
})

test("artist / playlist / generated_mix sessions → autoplay engine", () => {
  for (const type of ["artist", "playlist", "generated_mix"]) {
    const r = planRefill(type, "completed")
    assert.equal(r.engine, "autoplay", `engine for source_type=${type}`)
  }
})

test("undefined source_type (no session) → autoplay (safe default)", () => {
  const r = planRefill(undefined, "skipped")
  assert.equal(r.engine, "autoplay")
  assert.equal(r.sendEvent, false)
})
