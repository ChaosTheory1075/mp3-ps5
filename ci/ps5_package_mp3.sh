#!/usr/bin/env bash
# Max Payne 3 for PS5: link the PS5 build and package it as a PS5 homebrew app
# folder (eboot.bin, sce_module/libc.prx, sce_sys), the way gta4-ps5's
# ps5/tools/ps5_package.sh packages GTA IV.
#
#   ci/ps5_package_mp3.sh <cmake build dir> <ps5-plume-triangle checkout> <gta4-ps5 checkout> [title id]
#
# No game files are packaged: they go in the app's game/ folder (README.txt).
set -euo pipefail

build=$(realpath "${1:?cmake build dir}")
tri=$(realpath "${2:?ps5-plume-triangle checkout}")
fw=$(realpath "${3:?gta4-ps5 checkout}")
title_id=${4:-PPSA95454}
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ps5dir=$fw/ps5

sdk_root=${PS5_PAYLOAD_SDK:-$tri/.deps/native/ps5-payload-sdk}
radv_archive=${RADV_ARCHIVE:-$tri/.deps/native/radv-release/lib/libvulkan_radeon.ps5.a}
tool=$tri/build/host/ps5-native-tool
for f in "$sdk_root/bin/prospero-lld" "$radv_archive" "$tool" "$tri/build/obj/app_crt.o" \
         "$tri/build/obj/app_cpp_runtime.o" "$tri/build/stubs/libSceAgc.so" "$tri/runtime/libc.prx"; do
    [[ -e $f ]] || { echo "missing $f (build ps5-plume-triangle first)" >&2; exit 2; }
done

stage=$build/ps5-link
mkdir -p "$stage/build"
ln -sfn "$tri/tooling" "$stage/tooling"
ln -sfn "$tri/vendor" "$stage/vendor"

# The objects and static libraries CMake links into mp3, in its order.
link_cmd=$(ninja -C "$build" -t commands mp3 | tail -1)
mapfile -t inputs < <(cd "$build" && for w in $link_cmd; do
    case $w in
        */libz.a) ;;  # (RADV's archive has zlib in it)
        *.o|*.a) [[ $w = /* ]] && echo "$w" || echo "$build/$w" ;;
    esac
done)
echo "==> [link] ${#inputs[@]} objects and libraries from CMake"

export PS5_CLANG=${PS5_CLANG:-$(command -v clang-19 || command -v clang)}
source "$tri/tools/radv-link.sh"
radv_link_recipe "$stage" "$sdk_root" "$radv_archive"
"$PS5_CLANG" --target=x86_64-sie-ps5 -fPIC -fno-plt -fno-stack-protector \
    -isysroot "$sdk_root" -isystem "$sdk_root/target/include" \
    -O2 -c "$ps5dir/tools/ps5_emutls.c" -o "$stage/build/ps5-emutls-cxa.o"
"$sdk_root/bin/prospero-clang" -c -O2 -fno-visibility-from-dllstorageclass -o "$stage/build/ps5_stat.o" "$ps5dir/tools/ps5_stat.c"

link_pie() {  # extra objects, if any
    "$sdk_root/bin/prospero-lld" "${radv_linker_script[@]}" --eh-frame-hdr "${radv_link_flags[@]}" \
        --version-script "$tri/tooling/native/app-symbols.map" --exclude-libs=ALL \
        --wrap=stat --wrap=lstat \
        -e _start -o "$stage/build/llvm-pie.elf" \
        "$tri/build/obj/app_crt.o" "$tri/build/obj/app_cpp_runtime.o" "$stage/build/ps5_stat.o" "$@" \
        --start-group "${inputs[@]}" --end-group \
        "$tri/build/stubs/libSceAgc.so" "$tri/build/stubs/libSceAgcDriver.so" \
        "${radv_link_inputs[@]}" \
        --as-needed "$sdk_root"/target/lib/*.so
}
make_eboot() {
    "$tool" link --in "$stage/build/llvm-pie.elf" --out "$stage/build/eboot.elf" \
        --stub-dir "$sdk_root/target/lib" --stub "$tri/build/stubs/libSceAgc.so" \
        --stub "$tri/build/stubs/libSceAgcDriver.so" --module-sdk 0x02000009 \
        --companion-sdk 0x08050001 --file-name eboot.elf
}

echo "==> [link] Max Payne 3 + RADV into a PS5 PIE ELF"
link_pie

echo "==> [package] eboot.bin for $title_id"
if ! make_eboot 2> "$stage/build/eboot.err"; then
    cat "$stage/build/eboot.err" >&2
    grep -q "no room for PS5 process parameters" "$stage/build/eboot.err" || exit 1
    echo "==> [link] again with 2 KB of RELRO padding (room for the process parameters)"
    "$sdk_root/bin/prospero-clang" -c -O2 -fPIC -fno-visibility-from-dllstorageclass \
        -o "$stage/build/ps5_relro_pad.o" "$ps5dir/tools/ps5_relro_pad.c"
    link_pie "$stage/build/ps5_relro_pad.o"
    make_eboot
fi

app=$build/ps5-app/$title_id
rm -rf -- "$app"
mkdir -p "$app/sce_sys" "$app/sce_module" "$app/dlc" "$app/saves" "$app/logs"
"$tool" self --sign --in "$stage/build/eboot.elf" --out "$app/eboot.bin" --magic 0x1D3D154F
cp "$tri/runtime/libc.prx" "$app/sce_module/libc.prx"
cat > "$app/sce_sys/param.json" <<EOF
{
  "ageLevel": { "default": 0 },
  "applicationCategoryType": 0,
  "applicationDrmType": "free",
  "attribute": 0,
  "attribute2": 0,
  "attribute3": 262208,
  "conceptId": "${title_id:4}",
  "contentBadgeType": 1,
  "contentId": "UP9000-${title_id}_00-MAXPAYNE3PS50000",
  "contentVersion": "01.003.000",
  "downloadDataSize": 256,
  "gameIntent": { "permittedIntents": [ { "intentType": "launchActivity" } ] },
  "localizedParameters": {
    "defaultLanguage": "en-US",
    "en-US": { "titleName": "Max Payne 3 RePrescribed" }
  },
  "masterVersion": "01.00",
  "pubtools": { "creationDate": "$(date -u +%Y-%m-%d) 00:00:00", "loudnessSnd0": "-28.00", "toolVersion": "2.00" },
  "requiredSystemSoftwareVersion": "0x0000000000000000",
  "sdkVersion": "0x0000000000000000",
  "titleId": "${title_id}",
  "versionFileUri": ""
}
EOF
python3 "$here/make_sce_art.py" "$app/sce_sys"
cp "$here/../package/README.txt" "$app/README.txt"
# (the menu installs the discs into game/ and game2/ from install/)
mkdir -p "$app/install"
cp "$here/../package/install-README.txt" "$app/install/README.txt"
# the HD textures' list, for making them on the console from the Nexus pack (tools/mp3_texture_manifest.py)
mkdir -p "$app/hub"
cp "$here/../package/mp3-textures.txt" "$app/hub/mp3-textures.txt"
# The installer payload (ps5/payload/installer, Crackdown PS5's and GTA IV for PS5's): the app sends
# it to the payload loader so the install is written at the drive's full speed (mp3_install.cpp).
echo "==> [package] the installer payload"
payload_src=$here/../ps5/payload/installer
payload_build=$build/installer-payload
rm -rf "$payload_build" && mkdir -p "$payload_build" && cp -r "$payload_src/." "$payload_build/"
cp "$here/../mp3-recomp/src/ps5/mp3_textures_core.h" "$payload_build/include/"  # (the app's, shared)
make -C "$payload_build" PS5_PAYLOAD_SDK="$sdk_root"
mkdir -p "$app/payloads"
cp "$payload_build/mp3-ps5-installer.elf" "$app/payloads/mp3-ps5-installer.elf"
# (and SEND LOGS's: the same program with the logs' upload, libcurl, in it)
cp "$payload_build/mp3-ps5-logs.elf" "$app/payloads/mp3-ps5-logs.elf"

"$tool" self --inspect --file "$app/eboot.bin" | tail -3
echo "==> $app ($(stat -c %s "$app/eboot.bin") bytes)"
