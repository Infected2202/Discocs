// Type stubs for Capacitor packages.
// These packages are only installed in native (iOS/Android) builds.
// In web builds the modules are never imported at runtime (guarded by isNative()),
// but TypeScript still needs declarations to compile.

declare module "@capacitor/core" {
  export const Capacitor: {
    isNativePlatform(): boolean
  }
}

declare module "@capacitor/preferences" {
  export const Preferences: {
    get(opts: { key: string }): Promise<{ value: string | null }>
    set(opts: { key: string; value: string }): Promise<void>
    remove(opts: { key: string }): Promise<void>
  }
}

declare module "@capacitor/status-bar" {
  export const Style: { Dark: string; Light: string; Default: string }
  export const StatusBar: {
    setStyle(opts: { style: string }): Promise<void>
    setBackgroundColor(opts: { color: string }): Promise<void>
    setOverlaysWebView(opts: { overlay: boolean }): Promise<void>
  }
}
