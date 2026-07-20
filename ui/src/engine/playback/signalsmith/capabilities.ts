export type StretchFailureCode =
  | "audio-worklet-unavailable"
  | "wasm-unavailable"
  | "worklet-blocked"
  | "initialization-failed"

export interface StretchFailure {
  readonly code: StretchFailureCode
  readonly recoverable: boolean
  readonly message: string
}

export function detectStretchCapability(scope: typeof globalThis = globalThis): StretchFailure | null {
  if (!("AudioWorkletNode" in scope) || !("AudioContext" in scope)) {
    return { code: "audio-worklet-unavailable", recoverable: false, message: "AudioWorklet is unavailable" }
  }
  if (!("WebAssembly" in scope)) {
    return { code: "wasm-unavailable", recoverable: false, message: "WebAssembly is unavailable" }
  }
  return null
}

export function classifyStretchFailure(error: unknown): StretchFailure {
  const name = error instanceof DOMException || error instanceof Error ? error.name : ""
  const message = error instanceof Error ? error.message : String(error)
  const normalized = message.toLowerCase()
  if (name === "SecurityError" || normalized.includes("content security policy") || normalized.includes("blob:")) {
    return { code: "worklet-blocked", recoverable: false, message }
  }
  if (name === "CompileError" || normalized.includes("webassembly") || normalized.includes("wasm")) {
    return { code: "wasm-unavailable", recoverable: false, message }
  }
  if (name === "NotSupportedError" || normalized.includes("audioworklet")) {
    return { code: "audio-worklet-unavailable", recoverable: false, message }
  }
  return { code: "initialization-failed", recoverable: true, message }
}
