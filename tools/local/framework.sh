#!/bin/bash
# The framework source (gta4-ps5, set up) and the Crackdown PC recomp's patches, once.
set -eo pipefail
W=${MP3_WORK:-/root/mp3build}
mkdir -p "$W" && cd "$W"
if [ ! -f fw-pristine/.setup-done ]; then
  rm -rf fw-pristine
  git clone -q --depth 1 https://github.com/elripalda/gta4-ps5 fw-pristine
  (cd fw-pristine && bash ./setup.sh) > setup.txt 2>&1 || { tail -30 setup.txt; exit 1; }
  touch fw-pristine/.setup-done
fi
if [ ! -d cd/patches ]; then
  rm -rf cd && git init -q cd && git -C cd remote add origin https://github.com/saulob/crackdown-xbox360-recompiled
  git -C cd fetch -q --depth 1 origin 33ab1b632e38cb5c1c39a3d56c479537b9e0d185 && git -C cd checkout -q FETCH_HEAD
fi
echo FRAMEWORK READY
