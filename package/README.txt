Max Payne 3 RePrescribed
========================

Max Payne 3 (Xbox 360, Title Update 9) recompiled to run natively on the PS5,
at 30 or 60 fps, with its DLC, your saves and its achievements as trophies.
An unofficial fan project: no game files are included, you need your own copy
of Max Payne 3.

What you need
-------------
- A PS5 that runs homebrew apps from /data/homebrew/ or homebrew/ on an
  external drive.
- Your Max Payne 3 disc images (.iso, disc 1 and disc 2), or the discs' files.
- Title Update 9's package (tu00000001_00000000).
- Optional: the DLC packages.
- Optional: the Nexus HD texture pack (nexusmods.com/maxpayne3/mods/275, its
  main file and all its optional files), extracted on a PC.

Install
-------
1. Copy this whole PPSA95454 folder to the PS5's homebrew folder
   (/data/homebrew/, or homebrew/ on an external drive).
2. Put these in PPSA95454/install/ (see install/README.txt):
   - your two disc images (.iso, any names), or folders of the discs' files,
   - Title Update 9's package (required),
   - the DLC packages (optional),
   - the Nexus HD texture pack's folders (optional; install/README.txt has
     the steps: no merging needed).
   Extract any .7z / .rar / .zip on a PC first.
3. Open "Max Payne 3 RePrescribed". It installs what it finds, one at a time,
   with the progress on its HOME page (each disc image takes a few minutes).
   When PLAY lights up, you're set; the CONTENT page shows what's installed.
   At the end it asks whether to delete the files it installed from (X) or
   keep them (O).

Already have the discs' files extracted? Put each disc's folder in
PPSA95454/install/: the app tells the discs apart by themselves.

The menu
--------
HOME      PLAY; an install's progress; what's missing and where it goes.
CONTENT   disc 1, disc 2, Title Update 9, the DLC and the HD textures: installed or not,
          X installs one from install/, triangle (twice) uninstalls it.
SAVES     BACK UP SAVES (to install/backups/), SEND LOGS (for a bug report),
          IMPORT an Xbox 360 save package from install/, RESTORE one of the
          backups.
SETTINGS  DISC              which disc the game starts on (it asks for disc 2
                            itself when the story gets there).
          LANGUAGE          English, French, German, Spanish, Italian,
                            Brazilian Portuguese, Polish or Russian text and
                            subtitles.
          BUTTON ICONS      PlayStation (the PS3 version's) or Xbox.
          SKIP ANY CUTSCENE the skip button also skips the cutscenes the game
                            locks; their last seconds still play.
          SKIP INTRO        no Rockstar logo movie at the start.
          CHEAT MENU        in the game, L3 + R3 (both sticks pressed in) opens
                            the quick menu: god mode, infinite ammo, infinite
                            bullet time, one-shot kills, enemies that ignore
                            you (single player), the FPS counter and the
                            DualSense features.
          DUALSENSE         the light bar (Max's health, hits, painkillers,
                            bullet time), adaptive triggers and added rumble;
                            FEEDBACK STRENGTH low, medium or high.
          FRAME RATE        60 or 30 locked (the Xbox 360's).
          HD TEXTURES       the textures/ folder's HD textures, if installed.
          ANTI-ALIASING     off or FXAA.
          TEXTURE FILTERING 2x to 16x anisotropic.
          GPU HELPER        a second graphics thread (on); turn it off if
                            something draws wrong.
          FPS COUNTER       off, simple or detailed, in the top-left corner.
          MENU MUSIC        the menu song's level.
TROPHIES  every achievement, earned ones first, with their dates (filled in
          once the game has been started once).
CREDITS   who made it possible.
L1 / R1 change pages, O goes back to HOME. Settings are kept in menu.ini.

Your data (back these up before deleting or reinstalling the app!)
------------------------------------------------------------------
- PPSA95454/saves/          your game saves
- PPSA95454/UserData/       trophies (earned and their dates) and the shader cache
- PPSA95454/menu.ini        your settings
SAVES > BACK UP SAVES copies the saves, trophies and settings to
PPSA95454/install/backups/: copy that folder off the PS5. Deleting the
PPSA95454 folder deletes them too.

The first time each area is seen, its shaders are built and saved: the
first play-through can stutter now and then; later sessions load them.

Updating to a new build
-----------------------
Replace eboot.bin, sce_sys/, sce_module/ and payloads/ (and hub/ if it changed). Keep
game/, game2/, dlc/, textures/, install/, saves/, UserData/ and menu.ini.

Problems
--------
- PLAY stays grey: the HOME and CONTENT pages say what's missing.
- An install failed: the CONTENT page says why (not enough space, not the
  right file, ...). Fix it and press X on that row.
- Something draws wrong: SETTINGS > GPU HELPER off.
- The home screen tile, background or music didn't change after an update:
  restart the PS5 (it caches them).
- A log of every run is written to PPSA95454/logs/. For a report, SAVES >
  SEND LOGS uploads the last few to catbox.moe and shows a link: send that
  link with the report (anyone with it can read them). Without the internet
  or a payload loader, copy the newest log off the PS5 by FTP instead.

Credits
-------
Built on elripalda's GTA IV for PS5 (github.com/elripalda/gta4-ps5), saulob's
Crackdown Recompiled (github.com/saulob/crackdown-xbox360-recompiled), OZORDI's
Liberty Recompiled and vaduur's LibertyRecompAndroid, ReXGlue and Xenia.
HD textures from mdsxxx1's Max Payne 3 Texture Pack V4 on Nexus Mods (with
ElPicoleto's blood effects), built on the PS5 from your own download.
Hover music made with BlackBearReloaded's PS5 AT9 Converter.
Special thanks to Rockstar Games and Remedy Entertainment, for Max Payne.

Max Payne and all related names belong to their owners. This project is not
affiliated with or endorsed by Rockstar Games, Take-Two Interactive,
Remedy Entertainment, Microsoft or Sony.
