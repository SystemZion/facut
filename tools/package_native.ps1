param(
    [string]$FFmpegRoot = $env:FACUT_FFMPEG_ROOT,
    [string]$FacutExe,
    [string]$NativeBuild,
    [string]$OutputRoot,
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $FacutExe) { $FacutExe = Join-Path $ProjectRoot "dist\facut.exe" }
if (-not $NativeBuild) { $NativeBuild = Join-Path $ProjectRoot "build\native-win64" }
if (-not $OutputRoot) { $OutputRoot = Join-Path $ProjectRoot "dist" }
if (-not $FFmpegRoot) { throw "Pass -FFmpegRoot or set FACUT_FFMPEG_ROOT." }

$FacutExe = [System.IO.Path]::GetFullPath($FacutExe)
$NativeBuild = [System.IO.Path]::GetFullPath($NativeBuild)
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$FFmpegRoot = [System.IO.Path]::GetFullPath($FFmpegRoot)
$Bundle = Join-Path $OutputRoot "facut-windows-x64"
$Archive = Join-Path $OutputRoot "facut-windows-x64.zip"

foreach ($required in @(
    $FacutExe,
    (Join-Path $NativeBuild "facut-native.exe"),
    (Join-Path $FFmpegRoot "LICENSE.txt"),
    (Join-Path $ProjectRoot "native\THIRD_PARTY_NOTICES.md")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required package input was not found: $required"
    }
}

if ((Test-Path -LiteralPath $Bundle) -or (Test-Path -LiteralPath $Archive)) {
    if (-not $Overwrite) { throw "Package output already exists. Pass -Overwrite to replace it." }
    $resolvedOutput = $OutputRoot.TrimEnd('\') + '\'
    foreach ($target in @($Bundle, $Archive)) {
        $resolvedTarget = [System.IO.Path]::GetFullPath($target)
        if (-not $resolvedTarget.StartsWith($resolvedOutput, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove package output outside the selected output root."
        }
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $Bundle | Out-Null
Copy-Item -LiteralPath $FacutExe -Destination (Join-Path $Bundle "facut.exe")
Copy-Item -LiteralPath (Join-Path $NativeBuild "facut-native.exe") -Destination $Bundle

$prefixes = @("avcodec-", "avdevice-", "avfilter-", "avformat-", "avutil-", "swresample-", "swscale-")
$dlls = Get-ChildItem -LiteralPath $NativeBuild -Filter "*.dll" | Where-Object {
    $name = $_.Name.ToLowerInvariant()
    $prefixes.Where({ $name.StartsWith($_) }).Count -gt 0
}
$present = @($dlls | ForEach-Object { $_.Name.Split('-')[0].ToLowerInvariant() } | Sort-Object -Unique)
$missing = @($prefixes | ForEach-Object { $_.TrimEnd('-') } | Where-Object { $_ -notin $present })
if ($missing.Count -gt 0) { throw "Native build is missing FFmpeg DLLs: $($missing -join ', ')" }
$dlls | Copy-Item -Destination $Bundle

Copy-Item -LiteralPath (Join-Path $ProjectRoot "native\THIRD_PARTY_NOTICES.md") -Destination $Bundle
Copy-Item -LiteralPath (Join-Path $FFmpegRoot "LICENSE.txt") -Destination (Join-Path $Bundle "FFMPEG_LICENSE.txt")
Compress-Archive -Path (Join-Path $Bundle "*") -DestinationPath $Archive -CompressionLevel Optimal

$hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
[pscustomobject]@{
    bundle = $Bundle
    archive = $Archive
    sha256 = $hash
    files = @(Get-ChildItem -LiteralPath $Bundle -File | Select-Object -ExpandProperty Name)
}
