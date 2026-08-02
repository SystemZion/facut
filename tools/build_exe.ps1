param(
    [string]$Python = "python",
    [string]$FFmpeg = "",
    [string]$FFprobe = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build"
$ResolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$BundledTools = Join-Path $ProjectRoot "vendor\ffmpeg"

if (-not $FFmpeg) {
    $CandidateFFmpeg = Join-Path $BundledTools "ffmpeg.exe"
    if (Test-Path -LiteralPath $CandidateFFmpeg -PathType Leaf) {
        $FFmpeg = $CandidateFFmpeg
    }
}
if (-not $FFprobe) {
    $CandidateFFprobe = Join-Path $BundledTools "ffprobe.exe"
    if (Test-Path -LiteralPath $CandidateFFprobe -PathType Leaf) {
        $FFprobe = $CandidateFFprobe
    }
}

function Assert-ProjectChild([string]$Candidate) {
    $ResolvedCandidate = [System.IO.Path]::GetFullPath($Candidate)
    $Prefix = $ResolvedProjectRoot.TrimEnd('\') + '\'
    if (-not $ResolvedCandidate.StartsWith($Prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem operation outside project root: $ResolvedCandidate"
    }
}

if ($Clean) {
    Assert-ProjectChild $DistPath
    Assert-ProjectChild $BuildPath
    if (Test-Path -LiteralPath $DistPath) {
        Remove-Item -LiteralPath $DistPath -Recurse -Force
    }
    if (Test-Path -LiteralPath $BuildPath) {
        Remove-Item -LiteralPath $BuildPath -Recurse -Force
    }
}

$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--console",
    "--name", "facut",
    "--paths", (Join-Path $ProjectRoot "src"),
    "--collect-all", "typer",
    "--collect-all", "rich",
    "--collect-all", "pydantic",
    "--collect-data", "openpyxl",
    "--exclude-module", "faster_whisper",
    "--exclude-module", "ctranslate2",
    "--exclude-module", "av",
    "--exclude-module", "torch",
    "--exclude-module", "transformers",
    "--exclude-module", "tensorflow",
    "--exclude-module", "pandas",
    "--exclude-module", "numpy",
    "--exclude-module", "scipy",
    "--exclude-module", "sklearn",
    "--exclude-module", "matplotlib",
    "--exclude-module", "gradio",
    "--exclude-module", "pyarrow",
    "--collect-data", "facut"
)

if ($FFmpeg) {
    if (-not (Test-Path -LiteralPath $FFmpeg -PathType Leaf)) {
        throw "FFmpeg binary was not found: $FFmpeg"
    }
    $PyInstallerArgs += @("--add-binary", "$FFmpeg;facut_bin")
}
if ($FFprobe) {
    if (-not (Test-Path -LiteralPath $FFprobe -PathType Leaf)) {
        throw "FFprobe binary was not found: $FFprobe"
    }
    $PyInstallerArgs += @("--add-binary", "$FFprobe;facut_bin")
}
$PyInstallerArgs += (Join-Path $ProjectRoot "src\facut\__main__.py")

& $Python -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Exe = Join-Path $DistPath "facut.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Expected executable was not created: $Exe"
}

Write-Output $Exe
