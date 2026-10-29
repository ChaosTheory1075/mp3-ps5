#!/bin/bash
# Max Payne 3 PS5 build in WSL (Ubuntu 24.04), keeping what it can between builds:
#
#   wsl -d Ubuntu-24.04 -u root -- bash /mnt/c/.../mp3-ps5/tools/local/build.sh [repo] [out app folder]
#
# - tools/local/toolchain.sh builds the PS5 toolchain once ($W/tri).
# - The framework is cloned and set up once ($W/fw-pristine); each build patches
#   a fresh copy and syncs it into $W/ws/fw by content, so ninja and ccache only
#   rebuild what changed.
# - Codegen runs again only when its inputs change (cached in $W/gen/<hash>).
# - The game's code comes from your own files: MP3_XEX=<disc 1's default.xex> and
#   MP3_XEXP=<Title Update 9's disc001/default.xexp> (checked by their SHA-1s), or
#   else the private repo's assets/game.tar.enc with MP3_ASSET_KEY. Nothing leaves
#   the PC.
set -eo pipefail

repo=$(realpath "${1:-$(dirname "$0")/../..}")
app_out=${2:-}
W=${MP3_WORK:-/root/mp3build}
TITLE_ID=${TITLE_ID:-PPSA95454}
GTA4_PS5_REPO=https://github.com/elripalda/gta4-ps5
CD_PC_REPO=https://github.com/saulob/crackdown-xbox360-recompiled
CD_PC_COMMIT=33ab1b632e38cb5c1c39a3d56c479537b9e0d185
# disc 1's default.xex (0.0.0.6) and Title Update 9's disc001/default.xexp
XEX_SHA1=d36c6b8e6afc796b4c82f831aff6bf41ba494e6a
XEXP_SHA1=b446247658a7e7070eb7519e790beb61cb68878c
if [ -n "$MP3_XEX" ]; then
  echo "$XEX_SHA1  $MP3_XEX" | sha1sum -c --quiet || { echo "$MP3_XEX isn't disc 1's default.xex" >&2; exit 2; }
  echo "$XEXP_SHA1  $MP3_XEXP" | sha1sum -c --quiet || {
    echo "$MP3_XEXP isn't Title Update 9's disc001/default.xexp" >&2
    exit 2
  }
elif [ ! -f "$repo/assets/game.tar.enc" ]; then
  echo "set MP3_XEX and MP3_XEXP to your own game files" >&2
  exit 2
elif [ -z "$MP3_ASSET_KEY" ]; then
  key_file="$repo/../Extras/mp3-asset-key.txt"
  [ -f "$key_file" ] || key_file="$repo/../mp3-asset-key.txt"
  [ -f "$key_file" ] || { echo "set MP3_XEX and MP3_XEXP to your own game files" >&2; exit 2; }
  MP3_ASSET_KEY=$(tr -d '\r\n' < "$key_file")
fi
export MP3_ASSET_KEY
export CCACHE_DIR=$W/ccache
step() { echo "==> $(date +%T) $*"; }

[ -d "$W/tri/.deps/native/ps5-payload-sdk" ] || { echo "run tools/local/toolchain.sh first" >&2; exit 2; }
mkdir -p "$W/ws" "$W/gen" "$CCACHE_DIR"
ccache -M 30G > /dev/null

# ---- the workspace: the repo's files (not .git), with tri, fw and out kept ----
step "workspace"
rsync -a --delete --exclude .git --exclude /tri --exclude /fw --exclude /out --exclude /cd \
  --exclude /build-rexglue --exclude /private "$repo/" "$W/ws/"
cd "$W/ws"
ln -sfn "$W/tri" tri

# ---- framework: pristine once, then patched into fw-new, synced into fw ----
if [ ! -f "$W/fw-pristine/.setup-done" ]; then
  step "framework clone and setup (once)"
  rm -rf "$W/fw-pristine"
  git clone --depth 1 "$GTA4_PS5_REPO" "$W/fw-pristine"
  (cd "$W/fw-pristine" && bash ./setup.sh)
  touch "$W/fw-pristine/.setup-done"
fi
if [ ! -d "$W/cd/patches" ]; then
  step "Crackdown PC recomp patches (once)"
  rm -rf "$W/cd" && git init -q "$W/cd" && git -C "$W/cd" remote add origin "$CD_PC_REPO"
  git -C "$W/cd" fetch -q --depth 1 origin "$CD_PC_COMMIT" && git -C "$W/cd" checkout -q FETCH_HEAD
fi
step "framework patches"
rm -rf "$W/fw-new"
mkdir -p "$W/fw-new"
cp -a "$W/fw-pristine/." "$W/fw-new/"  # (the contents: fw-pristine may be a link to a shared one)
python3 ci/pc_patches.py "$W/fw-new" "$W/cd" > "$W/pc_patches.txt"
python3 ci/fw_perf_fixes.py "$W/fw-new" "$W/cd" > "$W/fw_perf_fixes.txt"
python3 ci/patch_framework.py "$W/fw-new" > "$W/patch_framework.txt"
M=glue/rexglue-sdk-main/mp3-recomp
rm -rf "$W/fw-new/$M"
cp -r mp3-recomp "$W/fw-new/$M"

# ---- codegen, only when its inputs changed ----
# (patch_framework.py only touches the codegen to dump the image: not an input)
gen_key=$( (git -C "$W/fw-pristine" rev-parse HEAD; cat ci/pc_patches.py ci/fw_perf_fixes.py \
  ci/mark_share_registers.py mp3-recomp/mp3_config.toml mp3-recomp/mp3_manifest.toml; echo "$XEX_SHA1 $XEXP_SHA1") |
  sha256sum | cut -c1-16)
if [ ! -d "$W/gen/$gen_key/generated" ]; then
  step "rexglue CLI (host)"
  rsync -a --checksum --delete "$W/fw-new/" "$W/fw-host/"
  cmake -S "$W/fw-host/glue/rexglue-sdk-main" -B "$W/build-rexglue" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-19 -DCMAKE_CXX_COMPILER=clang++-19 \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DCMAKE_C_FLAGS=-march=x86-64-v3 -DCMAKE_CXX_FLAGS=-march=x86-64-v3 \
    -DCMAKE_CXX_STANDARD=23 -DMP3_NO_GTA4_NATIVE=ON \
    -DREXGLUE_ENABLE_TRACY=OFF -DREXGLUE_BUILD_GTA4_RECOMP=OFF \
    -DREXGLUE_BUILD_TESTS=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 > "$W/rexglue-configure.txt"
  ninja -C "$W/build-rexglue" rexglue > "$W/rexglue-build.txt"
  REXGLUE=$(find "$W/build-rexglue" "$W/fw-host/glue/rexglue-sdk-main/out" -name rexglue -type f -perm -u+x 2>/dev/null | head -1)
  step "codegen"
  H=$W/fw-host/$M
  rm -rf "$H/generated" "$H/assets" && mkdir -p "$H/assets" "$W/private"
  if [ -n "$MP3_XEX" ]; then
    cp "$MP3_XEX" "$H/assets/default.xex" && cp "$MP3_XEXP" "$H/assets/default.xexp"
  else
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:MP3_ASSET_KEY -in assets/game.tar.enc | tar -xf - -C "$H/assets"
  fi
  REX_DUMP_IMAGE=$W/private/image.bin "$REXGLUE" --log-level info -f codegen "$H/mp3_manifest.toml" > "$W/codegen.txt" 2>&1 ||
    { tail -80 "$W/codegen.txt"; exit 1; }
  RBW=$W/fw-host/glue/rexglue-sdk-main/gta4-recomp/tools/read_before_write.py
  sed 's/gta4_recomp\.\*\.cpp/mp3_recomp.*.cpp/' "$RBW" > "$W/read_before_write.py"
  python3 "$W/read_before_write.py" "$H/generated" > "$W/read_before_write.txt" 2> "$W/rbw-total.txt"
  python3 ci/mark_share_registers.py "$W/read_before_write.txt" "$H/mp3_config.toml" "$W/private/image.bin"
  rm -rf "$H/generated"
  "$REXGLUE" --log-level info -f codegen "$H/mp3_manifest.toml" > "$W/codegen-pass2.txt" 2>&1 ||
    { tail -80 "$W/codegen-pass2.txt"; exit 1; }
  rm -rf "$H/assets"
  mkdir -p "$W/gen/$gen_key"
  mv "$H/generated" "$W/gen/$gen_key/generated"
  cp "$H/mp3_config.toml" "$W/gen/$gen_key/mp3_config.toml"
  # (only the latest few)
  ls -1t "$W/gen" | tail -n +4 | while read -r old; do rm -rf "$W/gen/$old"; done
fi
cp -r "$W/gen/$gen_key/generated" "$W/fw-new/$M/generated"
cp "$W/gen/$gen_key/mp3_config.toml" "$W/fw-new/$M/mp3_config.toml"

# ---- the PS5 program ----
step "sync framework (by content)"
rsync -a --checksum --delete "$W/fw-new/" "$W/ws/fw/"
rm -rf "$W/fw-new"
SDK=$W/tri/.deps/native/ps5-payload-sdk
if [ ! -f out/ps5/build.ninja ]; then
  step "configure (PS5)"
  cmake -S fw/ps5 -B out/ps5 -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE=$PWD/fw/ps5/cmake/ps5-toolchain.cmake -DPS5_PAYLOAD_SDK=$SDK \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DMP3_NO_GTA4_NATIVE=ON -DREXGLUE_ENABLE_FIDELITYFX=OFF \
    -DREXGLUE_ENABLE_TRACY=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DSDL_X11=OFF -DSDL_WAYLAND=OFF -DSDL_KMSDRM=OFF -DSDL_OPENGL=OFF -DSDL_OPENGLES=OFF \
    -DSDL_OSS=OFF -DSDL_ALSA=OFF -DSDL_PULSEAUDIO=OFF -DSDL_PIPEWIRE=OFF -DSDL_JACK=OFF \
    -DSDL_SNDIO=OFF -DSDL_HIDAPI=OFF -DSDL_DBUS=OFF -DSDL_IBUS=OFF -DSDL_LIBUDEV=OFF \
    -DSDL_VULKAN=OFF -DSDL_DUMMYVIDEO=ON -DSDL_DUMMYAUDIO=ON -DSDL_OFFSCREEN=OFF > "$W/configure.txt" 2>&1
fi
step "build (PS5)"
mkdir -p out
if ! ninja -C out/ps5 mp3 > out/build.txt 2>&1; then
  grep -E "error:|FAILED" out/build.txt | head -40
  exit 1
fi
tail -1 out/build.txt
step "package"
bash ci/ps5_package_mp3.sh out/ps5 tri fw "$TITLE_ID" > out/package.txt 2>&1 || { tail -40 out/package.txt; exit 1; }
llvm-nm-19 -n -C --defined-only out/ps5/ps5-link/build/llvm-pie.elf | grep -i ' t ' | gzip -9 > out/symbols.txt.gz || true
app=out/ps5/ps5-app/$TITLE_ID
# The user's own art and music (kept out of git): Extras/app-assets/ next to the repo, laid out as the
# app folder (sce_sys/icon0.png, pic0.dds, pic1.dds, snd0.at9; hub/background.jpg, hub/music/).
assets=${MP3_APP_ASSETS:-$repo/../Extras/app-assets}
[ -d "$assets" ] || assets=$repo/package/app-assets
if [ -d "$assets" ]; then
  cp -r "$assets/." "$app/"
  step "app assets from $assets"
fi
ls -l "$app/eboot.bin"
if [ -n "$app_out" ]; then
  # the app's own files over the folder (a new one, or an installed one: its game, DLC, textures,
  # saves, UserData and menu.ini are left as they are)
  mkdir -p "$app_out"
  cp -r "$app/." "$app_out/"
  cp out/symbols.txt.gz "$repo/../Extras/ps5-build-logs/symbols.txt.gz" 2>/dev/null || true
  step "app copied to $app_out"
fi
step "done"
