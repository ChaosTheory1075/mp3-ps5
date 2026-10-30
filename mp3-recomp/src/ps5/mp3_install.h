// Max Payne 3 RePrescribed (PS5) - the installer the menu's CONTENT and SAVES
// pages run (Crackdown PS5's cd_install, for Max Payne 3's two discs): what's
// in the app's install folder (<app>/install), what's installed, and the jobs
// that install, uninstall, import and back up, on a thread of their own.
//
//  - The discs: a disc image (.iso, any name) or a folder of a disc's files
//    (anywhere in install/), disc 1 into <app>/game, disc 2 into <app>/game2
//    (told apart by the disc's common/disc1.txt or disc2.txt).
//  - Title Update 9 (its package, any name): each disc's patch (disc001/,
//    disc002/) as default.xexp into that disc's folder, its other files into
//    <disc>/update/. Other title updates are named but not installed: the
//    program is TU9's.
//  - The DLC packages (any number): each into <app>/dlc/<its content id>/,
//    the name the Xbox 360 gives it, whatever the file is called.
//  - Saves: Xbox 360 save packages, into <app>/saves the way the runtime keeps
//    them; and the app's own backups (install/backups/...), restored.
//
// Everything is found from what it is (an image's file system, a package's
// header), never from its name. An install is written next to its place and
// moved in when complete, so an interrupted one leaves nothing half done; the
// source is deleted once it's in. Archives (.7z, .rar, .zip) are listed to be
// extracted on a PC first.
#pragma once

#include <rex/platform.h>
#if REX_PLATFORM_PS5

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

namespace mp3ps5::install {

namespace fs = std::filesystem;

enum class Kind {
  kGameImage,    // a disc image
  kGameFolder,   // a folder with default.xex
  kUpdate,       // Title Update 9
  kOtherUpdate,  // another title update: not installable
  kDlc,
  kTexturePack,  // the Nexus HD texture pack's (or the PC game's) archives: HD textures made from them
  kSave,         // an Xbox 360 save package
  kAvatarItem,   // an Xbox avatar item (from 5454086B/00009000): not used by the game
  kBackup,       // one of this app's backups
  kArchive,      // .7z / .rar / .zip: extract on a PC
  kOther,        // another game's package, or not known
};

struct Source {
  Kind kind = Kind::kOther;
  fs::path path;
  std::string label;  // for the menu
  int disc = 0;       // (kGameImage / kGameFolder) 1 or 2, 0 not known yet (an image: read when installed)
  bool installed = false;  // (kUpdate / kDlc) already installed from it: only to delete or keep
};

struct SaveInfo {
  std::string name;   // the save's folder (MP3_PROGRESSION, ...)
  int64_t modified = 0;  // (seconds since 1970)
};

struct Installed {
  bool disc[2] = {};    // game/, game2/
  bool update[2] = {};  // Title Update 9 in each
  int dlc = 0;          // DLC packs in dlc/
  bool textures = false;  // textures/ (made here, or copied in)
  int saves = 0;
  std::vector<SaveInfo> save_list;  // (the newest first)
};

fs::path Folder(const fs::path& app);  // <app>/install (made if missing)
Installed Check(const fs::path& app);
std::vector<Source> Scan(const fs::path& app);
// The ones installed without being asked, as the menu opens: the discs, TU9, the DLC.
bool Automatic(Kind kind);

enum class Remove { kDisc1, kDisc2, kUpdate, kDlc, kTextures };

// One job at a time, on its own thread.
class Job {
 public:
  bool Busy() const { return busy_.load(std::memory_order_acquire); }
  // (0..1; with Busy)
  float Progress() const;
  std::string Title() const;
  // The last finished job's outcome ("" none yet) and whether it failed.
  std::string Result() const;
  bool Failed() const { return failed_.load(std::memory_order_acquire); }
  // Bumped when a job finishes (the menu looks at the folders again).
  uint32_t Generation() const { return generation_.load(std::memory_order_acquire); }

  bool Install(const fs::path& app, const Source& source);
  bool Uninstall(const fs::path& app, Remove what);
  bool Backup(const fs::path& app);
  // <app>/logs uploaded for a bug report (by the installer payload), `note` (what went wrong, one
  // line) at their top; the result has the link.
  bool SendLogs(const fs::path& app, const std::string& note);
  // The link the last sending of the logs gave ("" none yet), without https://.
  std::string LogsLink() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return logs_link_;
  }

  // What the installs took from install/ (disc images, title update and DLC packages): kept there
  // until the user says to delete them (DeleteLeftovers) or to keep them (KeepLeftovers: noted in
  // install/kept.txt, so they aren't installed again). The texture pack's files go by themselves.
  std::vector<fs::path> Leftovers() const;
  uint64_t LeftoverBytes() const;
  bool DeleteLeftovers(const fs::path& app);
  void KeepLeftovers(const fs::path& app);

 private:
  template <typename F>
  bool Start(std::string title, F&& work);

  std::atomic<bool> busy_{false}, failed_{false};
  std::atomic<uint32_t> generation_{0};
  std::atomic<uint64_t> done_{0}, total_{0};
  mutable std::mutex mutex_;
  std::string title_, result_, logs_link_;
  std::vector<fs::path> leftovers_;

 public:
  void Leftover(const fs::path& path);
};

Job& TheJob();

// The HD textures (mp3_textures_install.cpp): the app's list of them, whether a pack is in install/,
// and the job's work (made from the pack's archives, which are deleted after).
fs::path TextureList(const fs::path& app);
int TexturePackFound(const fs::path& app);
bool BuildTextures(const fs::path& app, std::atomic<uint64_t>& done, std::atomic<uint64_t>& total,
                   std::string& result);

// The installer payload (mp3_install.cpp), for mp3_textures_install.cpp: one job run by it (kUnavailable:
// no payload loader), what it staged moved into place, a path as one inside the app's folder.
namespace payload {
enum class Ran { kUnavailable, kDone, kFailed };
inline constexpr const char* kJobRel = "UserData/mp3/.install-job";
inline constexpr const char* kStagingRel = "UserData/mp3/.install-staging";
Ran Run(const fs::path& app, const std::string& body, std::atomic<uint64_t>& done, std::atomic<uint64_t>& total,
        std::string& error);
bool Take(const fs::path& app, const std::string& rel, const fs::path& to, std::string& error);
std::string Inside(const fs::path& app, const fs::path& path);
inline fs::path JobFolder(const fs::path& app) { return app / kJobRel; }
}  // namespace payload

// Saves imported before the game made its profile folder wait in
// saves/_import/; at the game's start they move into saves/<profile>/.
void PlacePendingSaves(const fs::path& saves_root, uint64_t xuid);

}  // namespace mp3ps5::install

#endif  // REX_PLATFORM_PS5
