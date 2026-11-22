#!/usr/bin/env bash
# Builds the exact stable FFmpeg source as relocatable Windows LGPL DLLs.
set -euo pipefail
version=9.1
archive="ffmpeg-${version}.tar.xz"
source_sha=1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433
output="$(realpath -m -- "${1:?Pass an empty SDK output path}")"
if [[ -e "$output" ]]; then
    echo "SDK output already exists: $output" >&2
    exit 1
fi
workspace="$(mktemp -d)"
trap 'rm -rf -- "$workspace"' EXIT
cd "$workspace"
curl --fail --location --retry 4 --output "$archive" "https://ffmpeg.org/releases/$archive"
printf '%s  %s\n' "$source_sha" "$archive" | sha256sum --check -
tar -xf "$archive"
cd "ffmpeg-${version}"
./configure --prefix="$output" --arch=x86_64 --target-os=mingw32 \
    --cross-prefix=x86_64-w64-mingw32- --enable-cross-compile \
    --enable-shared --disable-static --enable-version3 \
    --disable-autodetect --disable-pthreads --enable-w32threads --disable-debug --disable-doc --disable-programs \
    --extra-ldflags=-static-libgcc
make -j"${FACUT_BUILD_JOBS:-4}"
make install

# Generate native MSVC COFF import libraries from the DLL exports, rather than
# relinking the libraries or shipping GNU archives renamed to .lib.
for component in avcodec avdevice avfilter avformat avutil swresample swscale; do
    shopt -s nullglob
    libraries=("$output/bin/$component-"*.dll)
    [[ "${#libraries[@]}" -eq 1 ]]
    dll="${libraries[0]}"
    gendef "$dll"
    llvm-dlltool -m i386:x86-64 -D "$(basename "$dll")" \
        -d "$(basename "${dll%.dll}").def" -l "$output/lib/$component.lib"
done
cp COPYING.LGPLv3 "$output/LICENSE.txt"
cp COPYING.GPLv3 "$output/CLI_GPLv3.txt"
cp "$workspace/$archive" "$output/$archive"
