#!/usr/bin/env python3
# [mp3] The Crackdown PC recomp's ReXGlue patches crackdown-ps5 applies (its
# ci/fw_perf_fixes.py): waits, register writes, submissions, occlusion queries,
# log-once. They apply to the pristine framework, so this runs first:
#   python3 ci/pc_patches.py fw cd   (cd: saulob/crackdown-xbox360-recompiled)
import subprocess
import sys
from pathlib import Path

fw, cd = Path(sys.argv[1]), Path(sys.argv[2])
sdk = "glue/rexglue-sdk-main"

# Correctness, from the fourth PS5 log and play: 0005 (codegen: vpkd3d128 packing in place),
# 0013 (texture results scaled by the fetch constant's exp_adjust, not the LOD bias bits),
# 0060 (XamEnumerate's item count zeroed on errors: the title walked off its stack).
for number in ("0005", "0015", "0017", "0019", "0020", "0024", "0027", "0038", "0060", "0061",
               "0062", "0064", "0066", "0079"):
    (patch,) = (cd / "patches" / "rexglue-sdk").glob(f"{number}-*.patch")
    subprocess.run(["git", "-C", str(fw), "apply", "-p1", f"--directory={sdk}", str(patch.resolve())], check=True)
    print(f"applied {patch.name}")

# 0053, on top of 0038 (offsets only, one hunk with fuzz 1): Direct3D 9 puts an occlusion
# query's begin and end in separate blocks, which the fork took for a mismatch and switched host
# queries off for good - every light's flare then drew as visible, through walls too.
for number in ("0053",):
    (patch,) = (cd / "patches" / "rexglue-sdk").glob(f"{number}-*.patch")
    subprocess.run(["patch", "-p1", "--fuzz=1", "--no-backup-if-mismatch", "-d", str(fw / sdk), "-i",
                    str(patch.resolve())], check=True)
    print(f"applied {patch.name}")


