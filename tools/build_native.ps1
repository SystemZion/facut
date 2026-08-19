param(
    [string]$VSRoot = $env:FACUT_VS_ROOT,
    [string]$FFmpegRoot = $env:FACUT_FFMPEG_ROOT,
    [ValidateSet("Release", "RelWithDebInfo", "Debug")]
    [string]$Configuration = "Release",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$NativeRoot = Join-Path $ProjectRoot "native"
$BuildRoot = Join-Path $ProjectRoot "build\native-win64"

if (-not $VSRoot) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere) {
        $VSRoot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    }
}
if (-not $VSRoot) {
    throw "Visual Studio was not found. Pass -VSRoot or set FACUT_VS_ROOT."
}
if (-not $FFmpegRoot) {
    throw "An LGPL shared FFmpeg development package is required. Pass -FFmpegRoot or set FACUT_FFMPEG_ROOT."
}

$VSRoot = [System.IO.Path]::GetFullPath($VSRoot)
$FFmpegRoot = [System.IO.Path]::GetFullPath($FFmpegRoot)
$vcvars = Join-Path $VSRoot "VC\Auxiliary\Build\vcvars64.bat"
$cmake = Join-Path $VSRoot "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$ninja = Join-Path $VSRoot "Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe"
foreach ($required in @($vcvars, $cmake, $ninja, (Join-Path $FFmpegRoot "include\libavformat\avformat.h"))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required native build dependency was not found: $required"
    }
}

if ($Clean -and (Test-Path -LiteralPath $BuildRoot)) {
    $resolvedProject = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    $resolvedBuild = [System.IO.Path]::GetFullPath($BuildRoot)
    if (-not $resolvedBuild.StartsWith($resolvedProject, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory outside the FACUT repository."
    }
    Remove-Item -LiteralPath $resolvedBuild -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

$configure = '"{0}" -S "{1}" -B "{2}" -G Ninja -DCMAKE_MAKE_PROGRAM="{3}" -DCMAKE_BUILD_TYPE={4} -DFACUT_FFMPEG_ROOT="{5}"' -f $cmake, $NativeRoot, $BuildRoot, $ninja, $Configuration, $FFmpegRoot
$build = '"{0}" --build "{1}" --config {2}' -f $cmake, $BuildRoot, $Configuration
$command = 'call "{0}" && {1} && {2}' -f $vcvars, $configure, $build
& cmd.exe /d /s /c $command
if ($LASTEXITCODE -ne 0) {
    throw "FACUT native build failed with exit code $LASTEXITCODE"
}

$exe = Join-Path $BuildRoot "facut-native.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Expected native executable was not created: $exe"
}
Write-Output $exe
