param(
    [string]$TinyGo = "",
    [string]$OutputName = "discocs.ndp"
)

$ErrorActionPreference = "Stop"
$pluginRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$dist = Join-Path $pluginRoot "dist"
$packageDir = Join-Path $dist "package"
$previousWasmOpt = $env:WASMOPT

if (-not $TinyGo) {
    $cmd = Get-Command tinygo -ErrorAction SilentlyContinue
    if ($cmd) {
        $TinyGo = $cmd.Source
    } elseif (Test-Path "C:\tinygo\bin\tinygo.exe") {
        $TinyGo = "C:\tinygo\bin\tinygo.exe"
    } else {
        throw "tinygo was not found. Add it to PATH or pass -TinyGo C:\tinygo\bin\tinygo.exe"
    }
}

if (-not $env:WASMOPT) {
    $wasmOpt = Get-Command wasm-opt -ErrorAction SilentlyContinue
    if (-not $wasmOpt) {
        $shimSource = Join-Path $pluginRoot "tools\wasm-opt-shim.cmd"
        $shimTarget = Join-Path $env:TEMP "discocs-wasm-opt-shim.cmd"
        Copy-Item -LiteralPath $shimSource -Destination $shimTarget -Force
        $env:WASMOPT = $shimTarget
        Write-Warning "wasm-opt was not found; using local no-op shim. Install Binaryen for optimized production builds."
    }
}

New-Item -ItemType Directory -Force $dist | Out-Null
if (Test-Path $packageDir) {
    Remove-Item -LiteralPath $packageDir -Recurse -Force
}
New-Item -ItemType Directory -Force $packageDir | Out-Null

Push-Location $pluginRoot
try {
    go mod download
    if ($LASTEXITCODE -ne 0) {
        throw "go mod download failed with exit code $LASTEXITCODE"
    }
    & $TinyGo build -o (Join-Path $packageDir "plugin.wasm") -target=wasip1 -scheduler=none -buildmode=c-shared .
    if ($LASTEXITCODE -ne 0) {
        throw "tinygo build failed with exit code $LASTEXITCODE"
    }
    Copy-Item -LiteralPath (Join-Path $pluginRoot "manifest.json") -Destination (Join-Path $packageDir "manifest.json")

    $packagePath = Join-Path $dist $OutputName
    $zipPath = Join-Path $dist ([System.IO.Path]::ChangeExtension($OutputName, ".zip"))
    if (Test-Path $packagePath) {
        Remove-Item -LiteralPath $packagePath -Force
    }
    if (Test-Path $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force
    Move-Item -LiteralPath $zipPath -Destination $packagePath -Force
    Write-Host "Built $packagePath"
} finally {
    Pop-Location
    $env:WASMOPT = $previousWasmOpt
}
