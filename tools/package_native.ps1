param(
    [string]$FFmpegRoot = $env:FACUT_FFMPEG_ROOT,
    [string]$FacutExe,
    [string]$NativeBuild,
    [string]$OutputRoot,
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $FacutExe) {
    $OneDirExe = Join-Path $ProjectRoot "dist\facut\facut.exe"
    $FacutExe = if (Test-Path -LiteralPath $OneDirExe -PathType Leaf) {
        $OneDirExe
    } else {
        Join-Path $ProjectRoot "dist\facut.exe"
    }
}
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
$FacutSourceRoot = Split-Path -Parent $FacutExe
$RuntimeDirectory = Join-Path $FacutSourceRoot "facut_runtime"
Copy-Item -LiteralPath $FacutExe -Destination (Join-Path $Bundle "facut.exe")
$BuildManifestSource = Join-Path $FacutSourceRoot "facut-build.json"
if (Test-Path -LiteralPath $BuildManifestSource -PathType Leaf) {
    Copy-Item -LiteralPath $BuildManifestSource -Destination $Bundle
}
if (Test-Path -LiteralPath $RuntimeDirectory -PathType Container) {
    Copy-Item -LiteralPath $RuntimeDirectory -Destination $Bundle -Recurse
}
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
$BuildManifest = if (Test-Path -LiteralPath $BuildManifestSource -PathType Leaf) {
    Get-Content -LiteralPath $BuildManifestSource -Raw | ConvertFrom-Json
} else { $null }
$ReleaseManifest = [ordered]@{
    schema = "facut-release/1.0"
    version = if ($BuildManifest) { $BuildManifest.version } else { "unknown" }
    commit = if ($BuildManifest) { $BuildManifest.commit } else { "unknown" }
    archive = "facut-windows-x64.zip"
    archive_sha256 = $hash
    executable_sha256 = (Get-FileHash -LiteralPath (Join-Path $Bundle "facut.exe") -Algorithm SHA256).Hash.ToLowerInvariant()
}
$ReleaseManifestPath = Join-Path $OutputRoot "facut-release.json"
$ReleaseManifest | ConvertTo-Json | Set-Content -LiteralPath $ReleaseManifestPath -Encoding utf8
$BundlePrefix = [System.IO.Path]::GetFullPath($Bundle).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
[pscustomobject]@{
    bundle = $Bundle
    archive = $Archive
    sha256 = $hash
    release_manifest = $ReleaseManifestPath
    files = @(Get-ChildItem -LiteralPath $Bundle -File -Recurse | ForEach-Object {
        $FilePath = [System.IO.Path]::GetFullPath($_.FullName)
        if (-not $FilePath.StartsWith($BundlePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Packaged file escaped the bundle root: $FilePath"
        }
        $FilePath.Substring($BundlePrefix.Length)
    })
}
