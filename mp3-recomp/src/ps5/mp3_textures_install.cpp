// Max Payne 3 RePrescribed (PS5) - the HD textures made on the console from the user's own PC
// files (mp3_textures_core.h has how): the app finds the files the list names in install/, the
// installer payload writes the textures at the drive's full speed (or the app itself, with no
// payload loader: slower), and the app puts them in place and deletes the pack's files.
#include "mp3_install.h"
#if REX_PLATFORM_PS5

#include <algorithm>
#include <cstdio>
#include <map>
#include <string>
#include <unordered_map>
#include <vector>

#include <fmt/format.h>
#include <xxhash.h>
#include <zlib.h>

#include <rex/logging.h>

#include "../install/gta4_install_io.h"
#include "mp3_textures_core.h"

namespace mp3ps5::install {

fs::path TextureList(const fs::path& app) { return app / "hub" / "mp3-textures.txt"; }

namespace {

using mp3tex::LowerPath;

// The user's files the list names, found in install/ by name and size: every such file, those whose
// path ends as the list's first. (The Nexus pack's patches have the size of the files they replace,
// and the pack may be merged or not: which copy is the one is told by the textures' hashes.)
void FindSources(const fs::path& app, mp3tex::Manifest& m) {
  std::unordered_map<std::string, std::vector<std::pair<fs::path, uint64_t>>> by_name;
  std::error_code ec;
  for (fs::recursive_directory_iterator it(Folder(app), fs::directory_options::skip_permission_denied, ec), end;
       !ec && it != end; it.increment(ec)) {
    std::error_code ec2;
    if (it->is_regular_file(ec2))
      by_name[LowerPath(it->path().filename().string())].push_back({it->path(), it->file_size(ec2)});
  }
  for (mp3tex::Src& s : m.sources) {
    const std::string rel = LowerPath(s.rel);
    const std::string base = rel.substr(rel.rfind('/') + 1);
    auto it = by_name.find(base);
    if (it == by_name.end()) continue;
    std::vector<fs::path> elsewhere;
    for (const auto& [path, size] : it->second) {
      if (size != s.size) continue;
      const std::string p = LowerPath(path.generic_string());
      if (p.size() >= rel.size() && p.compare(p.size() - rel.size(), rel.size(), rel) == 0)
        s.found.push_back(path);
      else
        elsewhere.push_back(path);
    }
    s.found.insert(s.found.end(), elsewhere.begin(), elsewhere.end());
  }
}

}  // namespace

int TexturePackFound(const fs::path& app) {
  // (cheap, for the menu: a Nexus pack or PC game's archives in install/ - the job matches them all)
  std::error_code ec;
  for (fs::recursive_directory_iterator it(Folder(app), fs::directory_options::skip_permission_denied, ec), end;
       !ec && it != end; it.increment(ec)) {
    const std::string n = LowerPath(it->path().filename().string());
    if (n == "pc.rpf" || n == "maxpayne3.exe") return 1;
    if (it.depth() >= 4) it.disable_recursion_pending();
  }
  return 0;
}

bool BuildTextures(const fs::path& app, std::atomic<uint64_t>& done, std::atomic<uint64_t>& total,
                   std::string& result) {
  mp3tex::Manifest m;
  if (!mp3tex::LoadManifest(TextureList(app), m)) {
    result = "The HD texture list (hub/mp3-textures.txt) is missing from the app.";
    return false;
  }
  FindSources(app, m);
  size_t found = 0, usable = 0;
  for (const auto& s : m.sources) found += !s.found.empty();
  const uint64_t bytes = mp3tex::UsableBytes(m, &usable);
  REXLOG_INFO("Max Payne 3 textures: {} of {} files of the list found, {} of {} textures can be made", found,
              m.sources.size(), usable, m.textures);
  if (!usable) {
    result = "No files of the HD texture pack were found in install/ (the Nexus pack's files, extracted).";
    return false;
  }
  total = bytes;
  std::error_code ec;
  const fs::path dest = app / "textures", tmp = app / "textures.installing";
  fs::remove_all(tmp, ec);
  size_t written = 0, bad = 0;
  std::string error;

  // the installer payload: the found files' list beside the job, then the job
  std::string files;
  for (size_t i = 0; i < m.sources.size(); ++i)
    for (const fs::path& f : m.sources[i].found) {
      const std::string inside = payload::Inside(app, f);
      if (!inside.empty()) files += fmt::format("{:x} {}\n", i, inside);
    }
  fs::create_directories(payload::JobFolder(app), ec);
  const std::string files_rel = std::string(payload::kJobRel) + "/texture-files.txt";
  payload::Ran ran = payload::Ran::kUnavailable;
  if (std::FILE* f = std::fopen((app / files_rel).string().c_str(), "wb")) {
    const bool ok = std::fwrite(files.data(), 1, files.size(), f) == files.size();
    if (std::fclose(f) == 0 && ok)
      ran = payload::Run(app,
                         std::string("kind=textures\nstaging=") + payload::kStagingRel +
                             "\nlist=hub/mp3-textures.txt\nfiles=" + files_rel +
                             "\nneeded=" + std::to_string(bytes + (bytes >> 4) + (256ull << 20)) + "\n",
                         done, total, error);
  }
  if (ran == payload::Ran::kDone) {
    if (!payload::Take(app, "textures", tmp, error)) ran = payload::Ran::kFailed;
  } else if (ran == payload::Ran::kUnavailable) {
    // (no payload loader: written by the app itself, slower)
    done = 0;
    if (mp3tex::Build(m, tmp, done, nullptr, written, bad, error)) ran = payload::Ran::kDone;
  }
  fs::remove(app / files_rel, ec);
  if (ran != payload::Ran::kDone) {
    fs::remove_all(tmp, ec);
    result = "Making the HD textures failed: " + error;
    return false;
  }
  // (the payload's count: the textures it wrote)
  if (!written)
    for (fs::recursive_directory_iterator it(tmp, ec), end; !ec && it != end; it.increment(ec))
      written += it->path().extension() == ".dds";
  if (!written) {
    fs::remove_all(tmp, ec);
    result = "None of the HD textures could be made: the pack's files aren't the ones the list was made from.";
    return false;
  }
  if (std::FILE* f = std::fopen((tmp / "installed.txt").string().c_str(), "wb")) {
    std::fprintf(f, "%zu HD textures, made on the PS5 from the list hub/mp3-textures.txt\n", written);
    std::fclose(f);
  }
  fs::remove_all(dest, ec);
  fs::rename(tmp, dest, ec);
  if (ec) {
    result = "Couldn't put the HD textures in place (" + ec.message() + ").";
    return false;
  }
  // the pack's files are no longer needed: each found file's top folder in install/ (when nothing else that
  // installs is in it), and the files themselves
  const fs::path root = Folder(app);
  std::map<fs::path, bool> tops;
  for (const auto& s : m.sources)
    for (const fs::path& f : s.found) {
      const fs::path rel = f.lexically_relative(root);
      if (rel.empty() || rel.begin() == rel.end()) continue;
      const fs::path top = root / *rel.begin();
      if (top != f) tops[top] = true;
    }
  for (const auto& [top, _] : tops) {
    bool other = false;
    for (fs::recursive_directory_iterator it(top, fs::directory_options::skip_permission_denied, ec), end;
         !ec && it != end && !other; it.increment(ec)) {
      const std::string ext = LowerPath(it->path().extension().string());
      other = ext == ".iso" || ext == ".xiso" || LowerPath(it->path().filename().string()) == "default.xex";
    }
    if (!other) fs::remove_all(top, ec);
  }
  for (const auto& s : m.sources)
    for (const fs::path& f : s.found) fs::remove(f, ec);
  char line[200];
  std::snprintf(line, sizeof(line), "%zu HD textures are installed%s.", written,
                written < m.textures ? " (the rest come from the PC game's own files)" : "");
  result = line;
  REXLOG_INFO("Max Payne 3 textures: {} written{}", written,
              bad ? fmt::format(", {} didn't match the list", bad) : std::string());
  return true;
}

}  // namespace mp3ps5::install

#endif  // REX_PLATFORM_PS5
