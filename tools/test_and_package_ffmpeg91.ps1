$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$sdk = (Resolve-Path -LiteralPath 'ffmpeg91-sdk').Path
$cliArchive = 'ffmpeg-9.1-essentials_build.7z'
$cliSha = '835b9a733445ba7a56a37a26cf7bde84e2f3ce7da2a8ec984276bb2abb1b922c'
$cliSource = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.1-essentials_build.7z'
curl.exe -fLsS --retry 4 --output $cliArchive $cliSource
$actual = (Get-FileHash -LiteralPath $cliArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $cliSha) { throw "FFmpeg CLI archive SHA-256 mismatch: $actual" }
New-Item -ItemType Directory -Path 'ffmpeg91-cli' | Out-Null
tar.exe -xf $cliArchive -C 'ffmpeg91-cli'
$cli = (Resolve-Path 'ffmpeg91-cli/ffmpeg-9.1-essentials_build').Path
$ff = Join-Path $cli 'bin/ffmpeg.exe'
$fp = Join-Path $cli 'bin/ffprobe.exe'
if ((& $ff -version | Select-Object -First 1) -notmatch '^ffmpeg version 9\.1(?:[-\s]|$)') {
    throw 'Expected exactly FFmpeg 9.1'
}
if ((& $fp -version | Select-Object -First 1) -notmatch '^ffprobe version 9\.1(?:[-\s]|$)') {
    throw 'Expected exactly FFprobe 9.1'
}
$env:FACUT_TEST_FFMPEG = $ff
$env:FACUT_TEST_FFPROBE = $fp

python -m pip install -e '.[dev,build]'
python -m pytest -q --timeout=60 --timeout-method=thread
./tools/build_native.ps1 -FFmpegRoot $sdk
./tools/build_exe.ps1 -FFmpeg $ff -FFprobe $fp -UseCurrentEnvironment

$dist = (Resolve-Path 'dist/facut').Path
Copy-Item -LiteralPath (Join-Path $sdk 'CLI_GPLv3.txt') -Destination (Join-Path $dist 'FFMPEG_CLI_GPLv3.txt')
$cliLicense = Get-ChildItem -LiteralPath $cli -File | Where-Object { $_.Name -match '^(license|copying)(\.txt)?$' } | Select-Object -First 1
if ($null -eq $cliLicense) { throw 'FFmpeg CLI distribution license missing' }
Copy-Item -LiteralPath $cliLicense.FullName -Destination (Join-Path $dist 'FFMPEG_CLI_BUILD_LICENSE.txt')
./tools/package_native.ps1 -FFmpegRoot $sdk

$zip = (Resolve-Path 'dist/facut-windows-x64.zip').Path
$manifest = Get-Content -LiteralPath 'dist/facut-release.json' -Raw | ConvertFrom-Json
if ($manifest.commit -ne (git rev-parse HEAD) -or $manifest.version -ne '1.0.2') { throw 'Incorrect release build identity' }
if ((Get-FileHash -LiteralPath $zip).Hash -ne $manifest.archive_sha256) { throw 'Packaged archive hash mismatch' }
Expand-Archive -LiteralPath $zip -DestinationPath 'facut-release-smoke'
$bundle = (Resolve-Path -LiteralPath 'facut-release-smoke').Path
$facut = Join-Path $bundle 'facut.exe'
$native = Join-Path $bundle 'facut-native.exe'
$embedded = Join-Path $bundle 'facut_runtime/facut_bin'
$bundledFF = Join-Path $embedded 'ffmpeg.exe'
$bundledFP = Join-Path $embedded 'ffprobe.exe'
if ((Get-FileHash -LiteralPath $bundledFF).Hash -ne (Get-FileHash -LiteralPath $ff).Hash) { throw 'Bundled FFmpeg mismatch' }
if ((Get-FileHash -LiteralPath $bundledFP).Hash -ne (Get-FileHash -LiteralPath $fp).Hash) { throw 'Bundled FFprobe mismatch' }
if ((Get-FileHash -LiteralPath $facut).Hash -ne $manifest.executable_sha256) { throw 'Packaged executable hash mismatch' }
$doctor = & $native --doctor | ConvertFrom-Json
if (($doctor.ffmpeg.avcodec -shr 16) -ne 63 -or ($doctor.ffmpeg.avformat -shr 16) -ne 63 -or
    ($doctor.ffmpeg.avutil -shr 16) -ne 61 -or
    $doctor.ffmpeg.license -notmatch '^(?:LGPL|(?:GNU )?Lesser General Public License) version 3 or later$' -or
    $doctor.ffmpeg.configuration -match '--enable-(gpl|nonfree)') {
    throw "Unexpected native FFmpeg ABI/license: $($doctor.ffmpeg | ConvertTo-Json -Compress)"
}
$doctor | ConvertTo-Json -Depth 4

# Exercise the actual extracted package without pointing its media CLI at the build tree.
$env:FACUT_TEST_FFMPEG = $null
$env:FACUT_TEST_FFPROBE = $null
$env:FACUT_FFMPEG = $null
$env:FACUT_FFPROBE = $null
$env:FACUT_NATIVE = $null
$env:FACUT_CONFIG = Join-Path $bundle 'smoke-config.toml'
New-Item -ItemType Directory -Force 'native-smoke' | Out-Null
& $bundledFF -hide_banner -loglevel error -f lavfi -i 'testsrc2=size=640x360:rate=30' -f lavfi -i 'sine=frequency=440:sample_rate=48000' -t 2 -c:v libx264 -c:a aac 'native-smoke/sample.mp4'
& $bundledFF -hide_banner -loglevel error -f lavfi -i 'color=c=blue:size=640x360' -frames:v 1 'native-smoke/photo.jpg'
& $facut --json native doctor
& $facut --json analyze batch 'native-smoke' --engine native --mode fast --output-directory 'native-smoke/results'
$result = Get-Content -Raw -Encoding UTF8 'native-smoke/results/native-results.json' | ConvertFrom-Json
$photo = @($result.results | Where-Object { $_.kind -eq 'image' })
$video = @($result.results | Where-Object { $_.kind -eq 'video' })
if ($photo.Count -ne 1 -or @($photo[0].representative_frames).Count -ne 1) { throw 'Still-image scan failed' }
if ($video.Count -ne 1 -or !$video[0].representative_frames -or !$video[0].waveform.peaks -or
    $video[0].waveform.sample_rate -ne 48000) { throw 'Video frames or original audio waveform missing' }

# Test installation with a fresh destination and explicitly excluded model weights.
$installed = Join-Path $env:RUNNER_TEMP 'facut91-installed-smoke'
& $facut --json install --directory $installed --no-path --exclude model
$installedFacut = Join-Path $installed 'facut.exe'
$installedNative = Join-Path $installed 'facut-native.exe'
$installedDoctor = & $installedNative --doctor | ConvertFrom-Json
if (($installedDoctor.ffmpeg.avcodec -shr 16) -ne 63) { throw 'Installed native ABI mismatch' }
if ((Get-FileHash -LiteralPath (Join-Path $installed 'facut_runtime/facut_bin/ffmpeg.exe')).Hash -ne (Get-FileHash -LiteralPath $ff).Hash) { throw 'Installed FFmpeg mismatch' }
if ((Get-FileHash -LiteralPath (Join-Path $installed 'facut_runtime/facut_bin/ffprobe.exe')).Hash -ne (Get-FileHash -LiteralPath $fp).Hash) { throw 'Installed FFprobe mismatch' }
foreach ($notice in @('THIRD_PARTY_NOTICES.md', 'FFMPEG_LICENSE.txt', 'FFMPEG_CLI_GPLv3.txt', 'FFMPEG_CLI_BUILD_LICENSE.txt')) {
    if (!(Test-Path -LiteralPath (Join-Path $installed $notice) -PathType Leaf)) { throw "Installed license missing: $notice" }
}
& $installedFacut --json native doctor
