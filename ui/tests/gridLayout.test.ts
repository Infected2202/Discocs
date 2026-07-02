import assert from "node:assert/strict"
import test from "node:test"
import { computeGridColumns } from "../src/components/media/gridLayout.ts"

test("grid columns match CSS auto-fill minmax packing", () => {
  // 160px min + 4px gap: (W + gap) / (min + gap)
  // 1000px → floor(1004 / 164) = 6
  assert.equal(computeGridColumns(1000, 160, 4), 6)
  // exactly two columns + one gap: 324px → floor(328/164) = 2
  assert.equal(computeGridColumns(324, 160, 4), 2)
  // just under a third column: 487px → floor(491/164) = 2 (not 3)
  assert.equal(computeGridColumns(487, 160, 4), 2)
})

test("grid columns never drop below one", () => {
  assert.equal(computeGridColumns(100, 160, 4), 1)
  assert.equal(computeGridColumns(0, 160, 4), 1)
  assert.equal(computeGridColumns(-50, 160, 4), 1)
})

test("wider cards yield fewer columns", () => {
  assert.equal(computeGridColumns(1000, 320, 4), 3)
})
