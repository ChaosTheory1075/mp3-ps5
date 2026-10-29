#!/bin/bash
# The PS5 toolchain in WSL (as ps5-build.yml's ps5-plume-triangle steps), once.
set -eo pipefail
W=${MP3_WORK:-/root/mp3build}
mkdir -p "$W" && cd "$W"
[ -d tri ] || git clone --recursive https://github.com/Nazky/ps5-plume-triangle tri
cd tri
if [ ! -f .deps/native/ps5-payload-sdk/bin/prospero-clang ] && [ ! -d .deps/native/ps5-payload-sdk/target ]; then
  bash tools/build.sh
fi
echo TOOLCHAIN READY
