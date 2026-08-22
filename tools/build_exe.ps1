param(
    [string]$Python = "python",
    [string]$FFmpeg = "",
    [string]$FFprobe = "",
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir",
    [switch]$UseCurrentEnvironment,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build"
$GeneratedSpecPath = Join-Path $BuildPath "generated-spec"
$ResolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$BundledTools = Join-Path $ProjectRoot "vendor\ffmpeg"
$PackagingVenv = Join-Path $BuildPath "packaging-venv"

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

$BuildPython = $Python
if (-not $UseCurrentEnvironment) {
    $VenvPython = Join-Path $PackagingVenv "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        New-Item -ItemType Directory -Force -Path $BuildPath | Out-Null
        & $Python -m venv $PackagingVenv
        if ($LASTEXITCODE -ne 0) { throw "Could not create isolated packaging environment." }
    }
    & $VenvPython -m pip install --disable-pip-version-check --quiet -e "${ProjectRoot}[build]"
    if ($LASTEXITCODE -ne 0) { throw "Could not install FACUT build dependencies in the isolated environment." }
    $BuildPython = $VenvPython
}
if ($FFmpeg) { $FFmpeg = [System.IO.Path]::GetFullPath($FFmpeg) }
if ($FFprobe) { $FFprobe = [System.IO.Path]::GetFullPath($FFprobe) }

New-Item -ItemType Directory -Force -Path $GeneratedSpecPath | Out-Null

$PyInstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--specpath", $GeneratedSpecPath,
    "--console",
    "--name", "facut",
    "--paths", (Join-Path $ProjectRoot "src"),
    "--collect-data", "openpyxl",
    "--collect-data", "opentimelineio",
    "--collect-submodules", "opentimelineio.adapters",
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
    "--exclude-module", "pytest",
    "--exclude-module", "pygame",
    "--exclude-module", "clr",
    "--exclude-module", "pythonnet",
    "--exclude-module", "win32com",
    "--collect-data", "facut"
)
if ($Mode -eq "onefile") {
    $PyInstallerArgs += "--onefile"
} else {
    $PyInstallerArgs += @("--onedir", "--contents-directory", "facut_runtime")
}
$PyInstallerArgs += @(
    "--add-data",
    "$(Join-Path $ProjectRoot 'src\facut\analysis\asr_worker.py');facut_worker"
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

& $BuildPython -m PyInstaller @PyInstallerArgs

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$Exe = if ($Mode -eq "onefile") {
    Join-Path $DistPath "facut.exe"
} else {
    Join-Path $DistPath "facut\facut.exe"
}
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Expected executable was not created: $Exe"
}

# Keep the selected packaging mode unambiguous. A stale one-file executable at
# dist\facut.exe otherwise looks newer and easier to launch than the current
# onedir build even though it may contain an older FACUT version.
if ($Mode -eq "onedir") {
    $StaleCounterpart = Join-Path $DistPath "facut.exe"
    Assert-ProjectChild $StaleCounterpart
    if (Test-Path -LiteralPath $StaleCounterpart -PathType Leaf) {
        Remove-Item -LiteralPath $StaleCounterpart -Force
    }
}

Write-Output $Exe
