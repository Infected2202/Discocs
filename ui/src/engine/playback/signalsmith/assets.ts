import packagedWorkletUrl from "signalsmith-stretch?url"
import type { SignalsmithStretchFactory } from "signalsmith-stretch"
import type { StretchNode } from "./types"

export interface SignalsmithAssetPlan {
  readonly module: "vite-dynamic-import"
  readonly workletUrl: string
  readonly wasm: "embedded-data-uri"
}

export type SignalsmithModuleLoader = () => Promise<{ default: SignalsmithStretchFactory }>

const loadSignalsmithModule: SignalsmithModuleLoader = () => import("signalsmith-stretch")

export function resolveSignalsmithWorkletUrl(assetUrl: string, baseUrl: string): string {
  return new URL(assetUrl, baseUrl).href
}

export function signalsmithAssetPlan(baseUrl: string): SignalsmithAssetPlan {
  return {
    module: "vite-dynamic-import",
    workletUrl: resolveSignalsmithWorkletUrl(packagedWorkletUrl, baseUrl),
    wasm: "embedded-data-uri",
  }
}

export async function createSignalsmithNode(
  context: AudioContext,
  options?: AudioWorkletNodeOptions,
  loader: SignalsmithModuleLoader = loadSignalsmithModule,
  baseUrl: string = document.baseURI,
): Promise<StretchNode> {
  const { default: createStretch } = await loader()
  createStretch.moduleUrl = signalsmithAssetPlan(baseUrl).workletUrl
  return createStretch(context, options)
}
