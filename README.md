# Max Payne 3 RePrescribed

Max Payne 3 (Xbox 360, 2012) recompiled to run natively on the PS5, by ChaosTheory1075.

> Built on [elripalda's GTA IV for PS5](https://github.com/elripalda/gta4-ps5), with GPU and timing
> work from [saulob's Crackdown Recompiled](https://github.com/saulob/crackdown-xbox360-recompiled).
> All the credit goes to them. Go star the original projects and support them.

An unofficial fan project, not affiliated with or endorsed by Rockstar Games, Take-Two Interactive,
Remedy Entertainment, Microsoft or Sony. **No game files are included:** you need your own copy of
Max Payne 3 for the Xbox 360.

## What's in it
- Max Payne 3 with Title Update 9, recompiled to run natively on the PS5 (not emulated)
- 60 FPS (the Xbox 360's 30 FPS lock lifted), with a 30 FPS mode
- The PC version's HD textures, made on the PS5 from the Nexus Mods texture pack
- FXAA and anisotropic filtering (2x to 16x)
- Both discs, and the DLC
- The game's achievements as PS5-style trophies, with their own page in the menu
- Installs the discs, Title Update 9, the DLC and the HD textures from your own files, at the drive's
  full speed
- Imports your Xbox 360 saves, backs up and restores saves
- A menu with HOME, CONTENT, SAVES, SETTINGS, TROPHIES and CREDITS pages, its own art and music

## What you need
- A jailbroken PS5 that runs homebrew apps, with a payload loader on port 9021 (etaHEN or elfldr)
- Your **own** Max Payne 3 for the Xbox 360: both discs, as `.iso` files or the discs' files
- Title Update 9 (required). The base game and other title updates aren't supported
- Optional: the DLC packages
- Optional: the [HD texture pack from Nexus Mods](https://www.nexusmods.com/maxpayne3/mods/275) (its
  main file and all its optional files)
- About 35 GB free while installing (more with the HD textures), about 16 GB once the disc images
  are deleted (24 GB with the HD textures)

## Which files work

| | File | Size |
|---|---|---|
| Disc 1 | `Max Payne 3 (World) (En,Fr,De,Es,It,Ru) (Disc 1) (Single Player Disc 1 and Multiplayer).iso` (any name) | 8,738,846,720 bytes |
| Disc 2 | `Max Payne 3 (World) (En,Fr,De,Es,It,Ru) (Disc 2) (Single Player Disc 2).iso` (any name) | 8,738,846,720 bytes |
| Title Update 9 | `tu00000001_00000000` | 18,796,544 bytes |
| DLC | the packages in `5454086B/00000002` (named like `40455109022BABD43D7C848FC1B8B486693C990954`) | |

The packages have no file extension; that's normal. Any file name works: the app tells what each
file is from what's in it, and tells disc 1 from disc 2 by itself.

## Install
1. Copy the `PPSA95454` folder from the release `.zip` to the PS5, wherever your homebrew loader
   looks (`/data/homebrew/`, `/mnt/ext0/homebrew/`, `/mnt/usb0/homebrew/`, ...).
2. Put your two disc images (or the discs' files), Title Update 9 and the DLC packages in
   `PPSA95454/install/`. Extract any `.7z`, `.rar` or `.zip` on a PC first.
3. Optional, the HD textures: download every file on the
   [mod's FILES page](https://www.nexusmods.com/maxpayne3/mods/275?tab=files), extract each one with
   7-Zip into its own folder (no need to merge them) and copy those folders into `install/` too.
4. Open **Max Payne 3 RePrescribed**. It installs everything it finds, one at a time, then asks
   whether to delete the files it installed from.
5. When PLAY lights up, you're set.

To import a save, put the Xbox 360 save package (a `CON` file copied off the 360's drive, such as
`MP3_PROGRESSION`) in `install/` and import it from the SAVES page. It loads the next time you press
PLAY.

See [package/README.txt](package/README.txt) for the rest: the menu's pages, your data and backups,
updating to a new build, and [package/install-README.txt](package/install-README.txt) for what goes
in `install/`.

## Build from source
The build runs on Ubuntu 24.04 (WSL works), as root, with your own game files:

```bash
bash tools/local/toolchain.sh   # the PS5 toolchain (ps5-plume-triangle), once
bash tools/local/framework.sh   # gta4-ps5's framework and the Crackdown PC recomp's patches, once
MP3_XEX=<disc 1's default.xex> MP3_XEXP=<Title Update 9's disc001/default.xexp> \
  bash tools/local/build.sh . <where to put the PPSA95454 folder>
```

The build checks the two files by their SHA-1s (`d36c6b8e...` and `b4462476...`). It applies
`ci/pc_patches.py`, `ci/fw_perf_fixes.py` and `ci/patch_framework.py` to gta4-ps5's framework,
recompiles the game (ReXGlue codegen), builds and signs the program and the installer payload
(`ps5/payload/installer`), and packages the app folder with the art and music in
`package/app-assets/`.

`tools/` has what the port was made with: `mp3_texture_manifest.py` (the HD texture list the app
carries, `package/mp3-textures.txt`), `xenos_disasm.py` and `xenos_emu.py` (Xbox 360 shaders),
`ppc_dis.py` and friends (the game's code), `stfs_extract.py` (Xbox 360 packages).

## Credits
- **elripalda**: [GTA IV for PS5](https://github.com/elripalda/gta4-ps5), the PS5 port this one is
  built on: the runtime, graphics and sound, the menu, trophies, the installer payload
- **saulob**: [Crackdown Recompiled](https://github.com/saulob/crackdown-xbox360-recompiled), whose
  ReXGlue patches and frame rate work this port's GPU and timing fixes come from
- **OZORDI** and **vaduur**: Liberty Recompiled and LibertyRecompAndroid, which GTA IV for PS5 starts from
- **ReXGlue** and the **Xenia** team: the static recompiler, the runtime and the Xbox 360 emulation
  it grew from
- **Crackdown: ReActivated** (ChaosTheory1075): the sister port whose menu, installer and trophies
  this one shares
- **mdsxxx1**: [Max Payne 3 Texture Pack V4](https://www.nexusmods.com/maxpayne3/mods/275) on Nexus Mods,
  the HD textures (with **ElPicoleto**'s blood effects and textures from ambientCG). Not included
  here: you download it from the mod's page, and the app builds the textures from it on your PS5
- **BlackBearReloaded**: the PS5 AT9 Converter (the home screen music)
- Built with the PS5 Payload SDK, Mesa RADV, Dear ImGui, stb_image, dr_mp3, zlib, xxHash, and the
  Oswald and Inter fonts (SIL Open Font License)
- Special thanks to **Rockstar Games** and **Remedy Entertainment**, for Max Payne

## License
The code in this repository is licensed under the GNU General Public License v3.0 (see
[LICENSE](LICENSE)), as GTA IV for PS5 is. Third-party components keep their own licenses. The
build also uses patches from saulob's Crackdown Recompiled, which is licensed under GPL-2.0-only.
No game files or textures are included, and Max Payne 3's game assets are not covered by this
license. Max Payne and all related names belong to their owners.
