// Max Payne 3 RePrescribed (PS5) - see mp3_install.h. Crackdown PS5's
// cd_install.cpp, for two discs, Title Update 9 and any number of DLC packs.
#include "mp3_install.h"
#if REX_PLATFORM_PS5

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <exception>
#include <fstream>
#include <memory>
#include <set>
#include <span>
#include <thread>
#include <utility>

#include <fmt/format.h>

#include <rex/filesystem.h>
#include <rex/filesystem/devices/disc_image_device.h>
#include <rex/filesystem/devices/stfs_container_device.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/file.h>
#include <rex/logging.h>
#include <rex/system/xam/content_manager.h>
#include <rex/system/xcontent.h>
#include <rex/system/xtypes.h>

// GTA IV for PS5's installer writing (header only): past the file cache in 8 MB pieces, flushed as
// it goes. Written through the cache, an install fills the console's memory after about 2 GB and
// everything waits on it.
#include "../install/gta4_install_io.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <cerrno>
#include <chrono>
#include <iterator>

#if defined(__PROSPERO__)
#include <fcntl.h>
#include <sys/mount.h>
#include <unistd.h>
extern "C" int _fstatfs(int fd, struct statfs* buf);
extern "C" int32_t sceKernelAvailableFlexibleMemorySize(size_t* size);
#endif

namespace mp3ps5::install {

using rex::X_STATUS;  // (X_STATUS_SUCCESS names it unqualified)

namespace {

constexpr uint32_t kTitleId = 0x5454086B;
constexpr const char* kTitleFolder = "5454086B";
constexpr uint32_t kTypeSave = 0x00000001, kTypeDlc = 0x00000002, kTypeUpdate = 0x000B0000,
                   kTypeAvatarItem = 0x00009000;
// Title Update 9's default.xexp (both discs' are this size)
constexpr uint64_t kUpdate9XexpSize = 3121152;
constexpr const char* kBackupMarker = "mp3-backup.txt";
const char* const kDiscFolders[2] = {"game", "game2"};

std::string Lower(std::string s) {
  for (char& c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

std::string Ext(const fs::path& p) { return Lower(p.extension().string()); }

// `dir`'s child named `name` in any case (empty: none).
fs::path FindChild(const fs::path& dir, const std::string& name) {
  std::error_code ec;
  const std::string want = Lower(name);
  for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec))
    if (Lower(it->path().filename().string()) == want) return it->path();
  return {};
}

// A disc's folder: which disc it is (common/disc1.txt or disc2.txt), 0 not Max Payne 3's.
int DiscOfFolder(const fs::path& dir) {
  if (FindChild(dir, "default.xex").empty()) return 0;
  const fs::path common = FindChild(dir, "common");
  if (common.empty()) return 0;
  if (!FindChild(common, "disc2.txt").empty()) return 2;
  if (!FindChild(common, "disc1.txt").empty()) return 1;
  return 0;
}

bool IsPackage(const fs::path& file) {
  char magic[4] = {};
  std::ifstream in(file, std::ios::binary);
  if (!in.read(magic, 4)) return false;
  return !std::memcmp(magic, "CON ", 4) || !std::memcmp(magic, "LIVE", 4) || !std::memcmp(magic, "PIRS", 4);
}

std::string Narrow(const std::u16string& s) {
  std::string out;
  for (char16_t c : s) {
    if (!c) break;
    out += c < 0x80 ? char(c) : '?';
  }
  return out;
}

struct Header {
  uint32_t title = 0, type = 0;
  std::u16string name;
};
bool ReadHeader(const fs::path& file, Header& h) {
  const auto header = rex::filesystem::StfsContainerDevice::ReadPackageHeader(file);
  if (!header) return false;
  h.title = uint32_t(header->metadata.execution_info.title_id);
  h.type = static_cast<uint32_t>(static_cast<rex::system::XContentType>(header->metadata.content_type));
  h.name = header->metadata.display_name(rex::system::XLanguage::kEnglish);
  return true;
}

// A package's name on the Xbox 360 (its content folder): the header's content id (20 bytes at
// 0x32C) in hex, then the title id's first byte ("...54" for Max Payne 3's DLC).
std::string ContentName(const fs::path& file) {
  unsigned char id[20] = {};
  std::ifstream in(file, std::ios::binary);
  if (!in.seekg(0x32C) || !in.read(reinterpret_cast<char*>(id), sizeof(id))) return {};
  std::string name;
  for (unsigned char b : id) name += fmt::format("{:02X}", b);
  return name + fmt::format("{:02X}", kTitleId >> 24);
}

std::string Gb(uint64_t bytes) {
  char s[32];
  std::snprintf(s, sizeof(s), "%.1f GB", double(bytes) / (1024.0 * 1024.0 * 1024.0));
  return s;
}

// ---- copying out of a disc image or a package ----

bool Skipped(const rex::filesystem::Entry* e) {
  // (the disc's console system update isn't the game's)
  return Lower(e->name()) == "$systemupdate";
}

uint64_t TotalSize(const rex::filesystem::Entry* e) {
  if (Skipped(e)) return 0;
  if (e->attributes() & rex::filesystem::kFileAttributeDirectory) {
    uint64_t total = 0;
    for (const auto& c : e->children()) total += TotalSize(c.get());
    return total;
  }
  return e->size();
}

class Copier {
 public:
  explicit Copier(std::atomic<uint64_t>& done) : done_(done), buffer_(8u << 20) {}

  // `e`'s children into `dest`.
  bool Children(rex::filesystem::Entry* e, const fs::path& dest, std::string& error) {
    std::error_code ec;
    fs::create_directories(dest, ec);
    for (const auto& c : e->children())
      if (!Skipped(c.get()) && !One(c.get(), dest / c->name(), error)) return false;
    return true;
  }

 private:
  bool One(rex::filesystem::Entry* e, const fs::path& dest, std::string& error) {
    if (e->attributes() & rex::filesystem::kFileAttributeDirectory) return Children(e, dest, error);
    rex::filesystem::File* in = nullptr;
    if (e->Open(rex::filesystem::FileAccess::kFileReadData, &in) != X_STATUS_SUCCESS || !in) {
      error = "couldn't read " + e->path();
      return false;
    }
    const size_t size = e->size();
    gta4::install::io::OutFile out;
    if (!out.Open(dest, &gather_, size)) {
      in->Destroy();
      error = "couldn't write " + dest.string() + " (is the drive full?)";
      return false;
    }
    size_t offset = 0;
    bool written = true;
    while (offset < size) {
      const size_t want = std::min(buffer_.size(), size - offset);
      size_t got = 0;
      in->ReadSync(std::span<uint8_t>(buffer_.data(), want), offset, &got);
      if (!got || !(written = out.Write(buffer_.data(), got))) break;
      offset += got;
      const uint64_t done = done_.fetch_add(got, std::memory_order_relaxed) + got;
      if (done >= logged_ + (512ull << 20)) {
        // (in the log every 512 MB, with the memory the console has left)
        logged_ = done;
        size_t flexible = 0;
#if defined(__PROSPERO__)
        sceKernelAvailableFlexibleMemorySize(&flexible);
#endif
        REXLOG_INFO("Max Payne 3 install: {} MB copied, {} MB of memory free", done >> 20, flexible >> 20);
      }
    }
    const bool ok = out.Close() && written && offset == size;
    in->Destroy();
    if (!ok) error = "couldn't copy " + e->path() + " (is the drive full?)";
    return ok;
  }

  std::atomic<uint64_t>& done_;
  std::vector<uint8_t> buffer_;
  gta4::install::io::AlignedBuffer gather_;  // (the writer's, kept between files)
  uint64_t logged_ = 0;
};

// The writing for the drive the app is on, as GTA IV for PS5 sets it: an internal (UFS) drive
// written past the file cache; an exFAT one through it, each file made its full size first
// (128 MB at a time) - a file that grows write by write there gets slower and slower.
void SetWritePolicy(const fs::path& app) {
  std::string type;
#if defined(__PROSPERO__)
  auto statfs_of = [](const fs::path& path, struct statfs& st) {
    std::error_code ec;
    fs::path probe;
    int fd = -1;
    if (fs::is_directory(path, ec)) {
      probe = path / ".drive-probe";
      fd = ::open(probe.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0666);
    } else {
      fd = ::open(path.c_str(), O_RDONLY);
    }
    if (fd < 0) return false;
    const bool ok = _fstatfs(fd, &st) == 0;
    ::close(fd);
    if (!probe.empty()) ::unlink(probe.c_str());
    return ok;
  };
  // (probed in install/: the app folder's top may not take a new file)
  struct statfs st {};
  std::string seen;
  if (statfs_of(app / "install", st) || statfs_of(app, st)) {
    type = st.f_fstypename;
    seen = type + " from " + st.f_mntfromname;
    // (/app0 is a nullfs mount of the app's real folder: the drive under it)
    if (type == "nullfs") {
      const std::string from = st.f_mntfromname;
      struct statfs lower {};
      if (statfs_of(fs::path(from), lower)) {
        type = lower.f_fstypename;
      } else {
        // (out of the app's reach: told from where it is - /data is the internal drive)
        type = from.rfind("/data", 0) == 0 || from.rfind("/user/data", 0) == 0 ? "ufs"
               : from.rfind("/mnt/", 0) == 0                                  ? "exfatfs"
                                                                               : "";
      }
    }
  }
  REXLOG_INFO("Max Payne 3 install: drive check: {}", seen.empty() ? "no answer" : seen);
#endif
  auto& policy = gta4::install::io::Policy();
  // (not known: the internal drive's way, past the cache - where it isn't allowed, the writer
  // goes through the cache by itself)
  const bool internal = type == "ufs" || type.empty();
  policy.preallocate = !internal;
  policy.reserve_step = 128ull << 20;
  policy.direct = internal;
  policy.threads = 1;
  REXLOG_INFO("Max Payne 3 install: the app's drive is {} ({} writes{})", type.empty() ? "not known" : type,
              policy.direct ? "direct" : "cached", policy.preallocate ? ", files sized first" : "");
}

bool EnoughSpace(const fs::path& app, uint64_t need, std::string& error) {
  std::error_code ec;
  const auto space = fs::space(app, ec);
  if (!ec && space.available < need + (256ull << 20)) {
    error = "Not enough free space: it needs " + Gb(need) + ", the drive has " + Gb(space.available) + ".";
    return false;
  }
  return true;
}

void RemoveSource(const fs::path& path) {
  std::error_code ec;
  fs::remove_all(path, ec);
  if (ec) REXLOG_WARN("Max Payne 3 install: couldn't remove {} ({})", path.string(), ec.message());
}

// ---- the installer payload ----
//
// The PS5 lets an app write about a gigabyte quickly, then slows its writing to a couple of MB/s
// (an install from inside the app took most of an hour). So the big copies are written by the
// installer payload (ps5/payload/installer, GTA IV for PS5's), which the app sends to the
// console's payload loader on 127.0.0.1:9021 (etaHEN, elfldr, ...): a process of its own, which
// writes at the drive's full speed. The app leaves it a job in its own folder and follows its
// status.txt there; the payload writes into the staging folder, and the app checks what it wrote
// and moves it into place. With no payload loader, the app copies by itself (slowly).

constexpr const char* kPayloadElf = "payloads/mp3-ps5-installer.elf";
constexpr const char* kLogsElf = "payloads/mp3-ps5-logs.elf";  // (the same with libcurl: SEND LOGS)
constexpr const char* kJobRel = "UserData/mp3/.install-job";
constexpr const char* kStagingRel = "UserData/mp3/.install-staging";

fs::path JobDir(const fs::path& app) { return app / kJobRel; }
fs::path Staging(const fs::path& app) { return app / kStagingRel; }

// `path` as a path inside the app's folder ("install/x.iso"), or "" when it isn't in it.
std::string InsideApp(const fs::path& app, const fs::path& path) {
  const fs::path rel = path.lexically_normal().lexically_relative(app.lexically_normal());
  if (rel.empty() || rel.is_absolute()) return {};
  const std::string first = rel.begin()->string();
  if (first == ".." || first == ".") return {};
  return rel.generic_string();
}

bool ReadText(const fs::path& path, std::string& out) {
  std::ifstream in(path, std::ios::binary);
  if (!in) return false;
  out.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
  return true;
}

// Whole, at once: written beside, then renamed over (never read half done).
bool WriteText(const fs::path& path, const std::string& text) {
  const fs::path tmp = path.string() + ".tmp";
  {
    std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
    if (!out.write(text.data(), std::streamsize(text.size()))) return false;
  }
  std::error_code ec;
  fs::rename(tmp, path, ec);
  return !ec;
}

std::string Field(const std::string& text, const char* key) {
  const std::string k = std::string(key) + "=";
  size_t at = 0;
  while (at < text.size()) {
    size_t end = text.find('\n', at);
    if (end == std::string::npos) end = text.size();
    if (text.compare(at, k.size(), k) == 0) return text.substr(at + k.size(), end - at - k.size());
    at = end + 1;
  }
  return {};
}

uint64_t Number(const std::string& text, const char* key) {
  return std::strtoull(Field(text, key).c_str(), nullptr, 10);
}

// Seconds since `path` was written (a lot when it isn't there).
double Age(const fs::path& path) {
  struct stat st {};
  if (::stat(path.c_str(), &st) != 0) return 1e9;
  return std::difftime(std::time(nullptr), st.st_mtime);
}

struct PayloadStatus {
  std::string token, phase, error, drive, result;
  uint64_t copy_done = 0, copy_total = 0, write_bytes = 0, write_ns = 0, beat = 0;
  bool Working() const { return phase != "done" && phase != "failed" && phase != "stopped"; }
};

bool ReadStatus(const fs::path& dir, PayloadStatus& s) {
  std::string text;
  if (!ReadText(dir / "status.txt", text)) return false;
  s.token = Field(text, "token");
  s.phase = Field(text, "phase");
  s.error = Field(text, "error");
  s.drive = Field(text, "drive");
  s.result = Field(text, "result");
  s.copy_done = Number(text, "copy_done");
  s.copy_total = Number(text, "copy_total");
  s.write_bytes = Number(text, "write_bytes");
  s.write_ns = Number(text, "write_ns");
  s.beat = Number(text, "beat");
  return !s.token.empty() && !s.phase.empty();
}

// The payload to the payload loader on 127.0.0.1:9021, which runs it as a process of its own.
bool SendToLoader(const std::string& elf, int& err) {
  err = 0;
  const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) {
    err = errno;
    return false;
  }
  timeval limit{};
  limit.tv_sec = 3;
  ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &limit, sizeof(limit));
  ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit));
#ifdef SO_NOSIGPIPE
  const int one = 1;  // (a loader that hangs up mustn't end the app)
  ::setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
#endif
#ifdef MSG_NOSIGNAL
  const int flags = MSG_NOSIGNAL;
#else
  const int flags = 0;
#endif
  sockaddr_in sa{};
  sa.sin_family = AF_INET;
  sa.sin_port = htons(9021);
  sa.sin_addr.s_addr = htonl(0x7F000001);
  if (::connect(fd, reinterpret_cast<sockaddr*>(&sa), sizeof(sa)) != 0) {
    err = errno;
    ::close(fd);
    return false;
  }
  size_t at = 0;
  while (at < elf.size()) {
    const ssize_t n = ::send(fd, elf.data() + at, elf.size() - at, flags);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) {
      err = errno;
      ::close(fd);
      return false;
    }
    at += size_t(n);
  }
  ::shutdown(fd, SHUT_WR);
  ::close(fd);
  return true;
}

// The job's files, once it's over ("running" stays: the payload's lock).
void ClearJob(const fs::path& dir) {
  std::error_code ec;
  for (const char* name : {"job.txt", "job.txt.tmp", "job.taken", "cancel", "cancel.tmp", "status.txt", "status.tmp"})
    fs::remove(dir / name, ec);
}

enum class Ran {
  kUnavailable,  // no payload loader (or no payload in the app): the app copies by itself
  kDone,         // the files are in the staging folder
  kFailed,
};

// One job (`body`: its fields, the token aside) written by the payload; `done`/`total` follow its
// copying. kDone: what it wrote is in the staging folder, marked as this job's; or, for a job
// that stages nothing (`result` given: sending the logs), what it answered.
Ran ByPayload(const fs::path& app, const std::string& body, std::atomic<uint64_t>& done,
              std::atomic<uint64_t>& total, std::string& error, std::string* result = nullptr,
              const char* elf_rel = kPayloadElf) {
  std::string elf;
  if (!ReadText(app / elf_rel, elf) || elf.size() < 64 || elf.compare(0, 4, "\x7F" "ELF") != 0) {
    REXLOG_WARN("Max Payne 3 install: the installer payload ({}) is missing from the app", elf_rel);
    return Ran::kUnavailable;
  }
  const fs::path dir = JobDir(app);
  std::error_code ec;
  fs::create_directories(dir, ec);
  // An earlier one still at it (the app was closed during an install): asked to stop, and waited for.
  // (While one runs, another payload leaves the job to it, and nobody would take this one.)
  for (int i = 0; i < 240; ++i) {
    PayloadStatus old;
    if (!ReadStatus(dir, old) || !old.Working() || Age(dir / "status.txt") > 15) break;
    if (i == 0) {
      REXLOG_INFO("Max Payne 3 install: an earlier install is still running ({}); stopping it", old.phase);
      WriteText(dir / "cancel", "stop\n");
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
  }
  ClearJob(dir);
  char made[48];
  std::snprintf(made, sizeof(made), "%lld-%lld", (long long)std::time(nullptr),
                (long long)(std::chrono::steady_clock::now().time_since_epoch().count() % 1000000));
  const std::string token = made;
  if (!WriteText(dir / "job.txt", "token=" + token + "\n" + body)) {
    REXLOG_WARN("Max Payne 3 install: couldn't write the install job in {}", dir.string());
    return Ran::kUnavailable;
  }
  int err = 0;
  if (!SendToLoader(elf, err)) {
    fs::remove(dir / "job.txt", ec);
    REXLOG_WARN("Max Payne 3 install: no payload loader answered on port 9021 (errno {}); installing in the app", err);
    return Ran::kUnavailable;
  }
  REXLOG_INFO("Max Payne 3 install: sent the installer payload ({} KB) to the payload loader on port 9021, job {}",
              elf.size() / 1024, token);
  using Clock = std::chrono::steady_clock;
  const auto sent = Clock::now();
  auto changed = sent, logged = sent;
  uint64_t beat = 0;
  bool running = false;
  for (;;) {
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
    const auto now = Clock::now();
    PayloadStatus s;
    if (ReadStatus(dir, s) && s.token == token) {
      if (!running)
        REXLOG_INFO("Max Payne 3 install: the payload started after {:.1f} s ({})",
                    std::chrono::duration<float>(now - sent).count(), s.drive);
      running = true;
      if (s.beat != beat) beat = s.beat, changed = now;
      if (s.copy_total) {
        total = s.copy_total;
        done = std::min(s.copy_done, s.copy_total);
      }
      if (now - logged >= std::chrono::seconds(10) || s.phase == "done") {
        logged = now;
        REXLOG_INFO("Max Payne 3 install: payload {}: {} of {} ({:.0f} MB/s writing)", s.phase, Gb(s.copy_done),
                    Gb(s.copy_total),
                    s.write_ns ? double(s.write_bytes) / 1048576.0 / (double(s.write_ns) / 1e9) : 0.0);
      }
      if (s.phase == "done") {
        std::string mark;
        ClearJob(dir);
        if (result) {
          *result = s.result;
          return Ran::kDone;
        }
        if (!ReadText(Staging(app) / ".token", mark) || mark != token) {
          error = "the installed files aren't from this install";
          return Ran::kFailed;
        }
        return Ran::kDone;
      }
      if (s.phase == "failed" || s.phase == "stopped") {
        error = s.error.empty() ? std::string("the installer payload stopped with an error") : s.error;
        ClearJob(dir);
        return Ran::kFailed;
      }
    }
    if (!running && now - sent > std::chrono::seconds(15)) {
      // Not started: the job taken back (one that starts late finds none), and the app copies by
      // itself. Taken but quiet: given longer.
      if (fs::remove(dir / "job.txt", ec) && !ec) {
        REXLOG_WARN("Max Payne 3 install: the payload loader didn't start the installer; installing in the app");
        return Ran::kUnavailable;
      }
      if (now - sent > std::chrono::seconds(45)) {
        WriteText(dir / "cancel", "stop\n");
        error = "the installer payload didn't start";
        return Ran::kFailed;
      }
    }
    if (running && now - changed > std::chrono::seconds(30)) {
      WriteText(dir / "cancel", "stop\n");
      error = "the installer payload stopped answering";
      return Ran::kFailed;
    }
  }
}

// What the payload wrote (<staging>/<rel>) moved to `to` (the staging folder emptied).
bool TakeStaged(const fs::path& app, const std::string& rel, const fs::path& to, std::string& error) {
  std::error_code ec;
  fs::remove_all(to, ec);
  fs::create_directories(to.parent_path(), ec);
  ec.clear();
  fs::rename(Staging(app) / rel, to, ec);
  if (ec) {
    error = "couldn't move the installed files into place (" + ec.message() + ")";
    return false;
  }
  fs::remove_all(Staging(app), ec);
  return true;
}

// A package's install by the payload, into `to`: kUnavailable, or done (true) / failed.
Ran PackageByPayload(const fs::path& app, const fs::path& package, const std::string& target, uint64_t size,
                     const fs::path& to, std::atomic<uint64_t>& done, std::atomic<uint64_t>& total,
                     std::string& error) {
  const std::string source = InsideApp(app, package);
  if (source.empty()) return Ran::kUnavailable;
  const Ran ran = ByPayload(app,
                            std::string("kind=package\nstaging=") + kStagingRel + "\nsource=" + source +
                                "\nsource_kind=package\nroot=\ntarget=" + target +
                                "\nneeded=" + std::to_string(size + (64ull << 20)) + "\n",
                            done, total, error);
  if (ran == Ran::kDone && !TakeStaged(app, target, to, error)) return Ran::kFailed;
  return ran;
}

}  // namespace

namespace payload {
Ran Run(const fs::path& app, const std::string& body, std::atomic<uint64_t>& done, std::atomic<uint64_t>& total,
        std::string& error) {
  switch (ByPayload(app, body, done, total, error)) {
    case install::Ran::kDone: return Ran::kDone;
    case install::Ran::kFailed: return Ran::kFailed;
    default: return Ran::kUnavailable;
  }
}
bool Take(const fs::path& app, const std::string& rel, const fs::path& to, std::string& error) {
  return TakeStaged(app, rel, to, error);
}
std::string Inside(const fs::path& app, const fs::path& path) { return InsideApp(app, path); }
}  // namespace payload

fs::path Folder(const fs::path& app) {
  std::error_code ec;
  fs::create_directories(app / "install", ec);
  return app / "install";
}

Installed Check(const fs::path& app) {
  Installed in;
  std::error_code ec;
  for (int d = 0; d < 2; ++d) {
    const fs::path game = app / kDiscFolders[d];
    in.disc[d] = !FindChild(game, "default.xex").empty();
    const fs::path xexp = FindChild(game, "default.xexp");
    in.update[d] = !xexp.empty() && fs::file_size(xexp, ec) == kUpdate9XexpSize;
  }
  for (fs::directory_iterator it(app / "dlc", ec), end; !ec && it != end; it.increment(ec)) {
    std::error_code ec2;
    if (it->is_directory(ec2) && it->path().extension() != ".installing" && !fs::is_empty(it->path(), ec2)) ++in.dlc;
  }
  ec.clear();
  in.textures = fs::is_directory(app / "textures", ec) && !fs::is_empty(app / "textures", ec);
  ec.clear();
  for (fs::directory_iterator x(app / "saves", ec), end; !ec && x != end; x.increment(ec)) {
    std::error_code ec2;
    for (fs::directory_iterator s(x->path() / kTitleFolder / "00000001", ec2), e2; !ec2 && s != e2; s.increment(ec2)) {
      if (!s->is_directory(ec2) || s->path().extension() == ".installing") continue;
      ++in.saves;
      // (its newest file's time: the folder's own changes on some drives only when files come and go)
      int64_t newest = 0;
      std::error_code ec3;
      for (fs::recursive_directory_iterator f(s->path(), ec3), fe; !ec3 && f != fe; f.increment(ec3)) {
        std::error_code ec4;
        if (!f->is_regular_file(ec4)) continue;
        const auto t = f->last_write_time(ec4);
        if (ec4) continue;
        const auto sys = std::chrono::time_point_cast<std::chrono::seconds>(
            t - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
        newest = std::max<int64_t>(newest, sys.time_since_epoch().count());
      }
      in.save_list.push_back({s->path().filename().string(), newest});
    }
  }
  std::sort(in.save_list.begin(), in.save_list.end(),
            [](const SaveInfo& a, const SaveInfo& b) { return a.modified > b.modified; });
  return in;
}

bool Automatic(Kind kind) {
  return kind == Kind::kGameImage || kind == Kind::kGameFolder || kind == Kind::kUpdate || kind == Kind::kDlc ||
         kind == Kind::kTexturePack;
}

namespace {

// install/kept.txt: "<size> <path in install/>" of each installed file the user kept.
std::set<std::string> Kept(const fs::path& root) {
  std::set<std::string> kept;
  std::ifstream in(root / "kept.txt");
  std::string line;
  while (std::getline(in, line))
    if (!line.empty()) kept.insert(line);
  return kept;
}
std::string KeptLine(const fs::path& root, const fs::path& file) {
  std::error_code ec;
  const uint64_t size = fs::file_size(file, ec);
  return std::to_string(ec ? 0 : size) + " " + file.lexically_relative(root).generic_string();
}

}  // namespace

std::vector<Source> Scan(const fs::path& app) {
  std::vector<Source> out;
  const fs::path root = Folder(app);
  const std::set<std::string> kept = Kept(root);
  std::error_code ec;
  // (the app's backups: install/backups/<one>/, with the marker file)
  const fs::path backups = root / "backups";
  for (fs::directory_iterator it(backups, ec), end; !ec && it != end; it.increment(ec)) {
    std::error_code ec2;
    if (it->is_directory(ec2) && fs::exists(it->path() / kBackupMarker, ec2))
      out.push_back({Kind::kBackup, it->path(), it->path().filename().string()});
  }
  ec.clear();
  if (TexturePackFound(app)) out.push_back({Kind::kTexturePack, root, "HD texture pack"});
  for (fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
       !ec && it != end; it.increment(ec)) {
    const fs::path p = it->path();
    std::error_code ec2;
    if (it->is_directory(ec2)) {
      // (a texture pack's or PC game's folder: its archives are the texture job's)
      if (!FindChild(p, "pc.rpf").empty() || !FindChild(p, "maxpayne3.exe").empty()) {
        it.disable_recursion_pending();
        continue;
      }
      if (p == backups) {
        it.disable_recursion_pending();
        continue;
      }
      // (one of this app's backups put straight in install/, as copied back from a PC or USB drive)
      if (fs::exists(p / kBackupMarker, ec2)) {
        out.push_back({Kind::kBackup, p, p.filename().string()});
        it.disable_recursion_pending();
        continue;
      }
      if (!FindChild(p, "default.xex").empty()) {
        Source s{Kind::kGameFolder, p, p.filename().string() + "/"};
        s.disc = DiscOfFolder(p);
        if (!s.disc) s.kind = Kind::kOther, s.label += "  (not a Max Payne 3 disc)";
        out.push_back(s);
        it.disable_recursion_pending();
      } else if (it.depth() >= 4) {
        it.disable_recursion_pending();
      }
      continue;
    }
    if (!it->is_regular_file(ec2)) continue;
    if (!kept.empty() && kept.count(KeptLine(root, p))) continue;  // (installed, and kept by the user)
    const std::string ext = Ext(p), file = p.filename().string();
    if (ext == ".iso" || ext == ".xiso") {
      out.push_back({Kind::kGameImage, p, file});
    } else if (ext == ".7z" || ext == ".rar" || ext == ".zip" || ext == ".001" || ext == ".gz" || ext == ".tar") {
      out.push_back({Kind::kArchive, p, file});
    } else if (IsPackage(p)) {
      Header h;
      if (!ReadHeader(p, h) || h.title != kTitleId) {
        out.push_back({Kind::kOther, p, file + "  (not a Max Payne 3 package)"});
        continue;
      }
      const std::string name = Narrow(h.name);
      if (h.type == kTypeUpdate) {
        // ("Max Payne 3 Title Update 9"?) the install checks the patch itself
        const bool other = name.find('#') != std::string::npos && name.find("#9") == std::string::npos;
        Source s{other ? Kind::kOtherUpdate : Kind::kUpdate, p, name.empty() ? file : name};
        const Installed in = Check(app);
        s.installed = !other && in.update[0] && in.update[1];
        out.push_back(s);
      } else if (h.type == kTypeDlc) {
        Source s{Kind::kDlc, p, name.empty() ? file : name};
        const std::string content = ContentName(p);
        s.installed = !content.empty() && fs::is_directory(app / "dlc" / content, ec2) &&
                      !fs::is_empty(app / "dlc" / content, ec2);
        out.push_back(s);
      } else if (h.type == kTypeSave) {
        out.push_back({Kind::kSave, p, name.empty() ? file : name});
      } else if (h.type == kTypeAvatarItem) {
        out.push_back({Kind::kAvatarItem, p, name.empty() ? file : name});
      } else {
        out.push_back({Kind::kOther, p, file});
      }
    }
  }
  // the discs first, then the update, then the DLC (the order they install in)
  std::stable_sort(out.begin(), out.end(), [](const Source& a, const Source& b) { return a.kind < b.kind; });
  return out;
}

// ---- the jobs ----

float Job::Progress() const {
  const uint64_t total = total_.load(std::memory_order_relaxed);
  return total ? std::min(1.0f, float(double(done_.load(std::memory_order_relaxed)) / double(total))) : 0.0f;
}

std::string Job::Title() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return title_;
}

std::string Job::Result() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return result_;
}

template <typename F>
bool Job::Start(std::string title, F&& work) {
  bool expected = false;
  if (!busy_.compare_exchange_strong(expected, true)) return false;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    title_ = title;
  }
  done_ = 0;
  total_ = 0;
  REXLOG_INFO("Max Payne 3 install: {}", title);
  std::thread([this, work = std::forward<F>(work)]() mutable {
    std::string result;
    bool ok = false;
    try {
      ok = work(result);
    } catch (const std::exception& e) {
      result = std::string("Failed: ") + e.what();
    }
    REXLOG_INFO("Max Payne 3 install: {} {}", ok ? "done:" : "FAILED:", result);
    {
      std::lock_guard<std::mutex> lock(mutex_);
      result_ = result;
    }
    failed_.store(!ok, std::memory_order_release);
    generation_.fetch_add(1, std::memory_order_acq_rel);
    busy_.store(false, std::memory_order_release);
  }).detach();
  return true;
}

bool Job::Install(const fs::path& app, const Source& source) {
  if (Busy()) return false;
  SetWritePolicy(app);
  switch (source.kind) {
    case Kind::kGameImage:
    case Kind::kGameFolder:
      return Start("Installing the game from " + source.label, [this, app, source](std::string& result) {
        std::error_code ec;
        const fs::path tmp = app / "game.installing";
        fs::remove_all(tmp, ec);
        if (source.kind == Kind::kGameImage) {
          rex::filesystem::DiscImageDevice disc("\\Device\\Mp3Install", source.path);
          if (!disc.Initialize()) {
            result = source.label + " isn't an Xbox 360 disc image.";
            return false;
          }
          rex::filesystem::Entry* root = disc.ResolvePath("");
          if (!root) {
            result = "Couldn't read " + source.label + ".";
            return false;
          }
          total_ = TotalSize(root);
          if (!EnoughSpace(app, total_, result)) return false;
          std::string error;
          const std::string image = InsideApp(app, source.path);
          const Ran ran = image.empty()
                              ? Ran::kUnavailable
                              : ByPayload(app,
                                          std::string("kind=game\nstaging=") + kStagingRel + "\nimage=" + image +
                                              "\nimage_kind=iso\ndisc_root=\nupdate=\nupdate_kind=none\nupdate_root=\n"
                                              "update_xexp=\nneeded=" + std::to_string(total_ + (256ull << 20)) + "\n",
                                          done_, total_, error);
          bool ok = false;
          if (ran == Ran::kDone)
            ok = TakeStaged(app, "game", tmp, error);
          else if (ran == Ran::kUnavailable)
            ok = Copier(done_).Children(root, tmp, error);
          if (!ok) {
            fs::remove_all(tmp, ec);
            result = "Installing from " + source.label + " failed: " + error;
            return false;
          }
          // (the disc's console system update isn't the game's: the payload copies it too)
          const fs::path system_update = FindChild(tmp, "$SystemUpdate");
          if (!system_update.empty()) fs::remove_all(system_update, ec);
        } else {
          fs::rename(source.path, tmp, ec);
          if (ec) {
            result = "Couldn't move " + source.label + " (" + ec.message() + ").";
            return false;
          }
          // (the disc's console system update isn't the game's)
          const fs::path system_update = FindChild(tmp, "$SystemUpdate");
          if (!system_update.empty()) fs::remove_all(system_update, ec);
        }
        const int disc = DiscOfFolder(tmp);
        if (!disc) {
          // (a folder that wasn't a disc goes back where it was)
          if (source.kind == Kind::kGameFolder) {
            fs::rename(tmp, source.path, ec);
          } else {
            fs::remove_all(tmp, ec);
          }
          result = source.label + " isn't a Max Payne 3 disc (no default.xex and common/disc1.txt or disc2.txt).";
          return false;
        }
        const fs::path game = app / kDiscFolders[disc - 1];
        // an installed title update stays
        for (const char* keep : {"default.xexp", "update"}) {
          const fs::path was = FindChild(game, keep);
          if (!was.empty() && FindChild(tmp, keep).empty()) fs::rename(was, tmp / keep, ec);
        }
        fs::remove_all(game, ec);
        fs::rename(tmp, game, ec);
        if (ec) {
          result = "Couldn't put disc " + std::to_string(disc) + " in place (" + ec.message() + ").";
          return false;
        }
        if (source.kind == Kind::kGameImage) Leftover(source.path);
        result = "Disc " + std::to_string(disc) + " is installed.";
        return true;
      });
    case Kind::kUpdate:
    case Kind::kOtherUpdate:
      return Start("Installing " + source.label, [this, app, source](std::string& result) {
        std::error_code ec;
        const fs::path tmp = app / "update.installing";
        fs::remove_all(tmp, ec);
        rex::filesystem::StfsContainerDevice package("\\Device\\Mp3Update", source.path);
        rex::filesystem::Entry* root = package.Initialize() ? package.ResolvePath("") : nullptr;
        if (!root) {
          result = "Couldn't read " + source.label + ".";
          return false;
        }
        total_ = TotalSize(root);
        std::string error;
        const Ran ran = PackageByPayload(app, source.path, "update", total_, tmp, done_, total_, error);
        const bool copied = ran == Ran::kDone || (ran == Ran::kUnavailable && Copier(done_).Children(root, tmp, error));
        if (!copied) {
          fs::remove_all(tmp, ec);
          result = "Installing " + source.label + " failed: " + error;
          return false;
        }
        // disc001/ and disc002/: each disc's patch and its other files
        for (int d = 0; d < 2; ++d) {
          const fs::path part = FindChild(tmp, d ? "disc002" : "disc001");
          const fs::path xexp = part.empty() ? fs::path() : FindChild(part, "default.xexp");
          if (xexp.empty() || fs::file_size(xexp, ec) != kUpdate9XexpSize) {
            fs::remove_all(tmp, ec);
            result = source.label + " isn't Title Update 9, which Max Payne 3 RePrescribed needs.";
            return false;
          }
        }
        for (int d = 0; d < 2; ++d) {
          const fs::path part = FindChild(tmp, d ? "disc002" : "disc001");
          const fs::path game = app / kDiscFolders[d], xexp = FindChild(part, "default.xexp");
          fs::create_directories(game, ec);
          const fs::path old_xexp = FindChild(game, "default.xexp");
          if (!old_xexp.empty()) fs::remove(old_xexp, ec);
          fs::rename(xexp, game / "default.xexp", ec);
          const fs::path old_update = FindChild(game, "update");
          if (!old_update.empty()) fs::remove_all(old_update, ec);
          fs::rename(part, game / "update", ec);
          if (ec) {
            fs::remove_all(tmp, ec);
            result = "Couldn't put Title Update 9 in place (" + ec.message() + ").";
            return false;
          }
        }
        fs::remove_all(tmp, ec);
        Leftover(source.path);
        result = "Title Update 9 is installed (both discs).";
        return true;
      });
    case Kind::kDlc:
      return Start("Installing " + source.label, [this, app, source](std::string& result) {
        std::error_code ec;
        const std::string name = ContentName(source.path);
        if (name.empty()) {
          result = "Couldn't read " + source.label + ".";
          return false;
        }
        const fs::path dest = app / "dlc" / name;
        const fs::path tmp = dest.string() + ".installing";
        fs::remove_all(tmp, ec);
        rex::filesystem::StfsContainerDevice package("\\Device\\Mp3Dlc", source.path);
        rex::filesystem::Entry* root = package.Initialize() ? package.ResolvePath("") : nullptr;
        if (!root) {
          result = "Couldn't read " + source.label + ".";
          return false;
        }
        total_ = TotalSize(root);
        std::string error;
        if (!EnoughSpace(app, total_, result)) return false;
        const Ran ran = PackageByPayload(app, source.path, "dlc/" + name, total_, tmp, done_, total_, error);
        const bool copied = ran == Ran::kDone || (ran == Ran::kUnavailable && Copier(done_).Children(root, tmp, error));
        if (!copied) {
          fs::remove_all(tmp, ec);
          result = "Installing " + source.label + " failed: " + error;
          return false;
        }
        fs::remove_all(dest, ec);
        fs::rename(tmp, dest, ec);
        if (ec) {
          result = "Couldn't put the DLC in place (" + ec.message() + ").";
          return false;
        }
        Leftover(source.path);
        result = source.label + " is installed.";
        return true;
      });
    case Kind::kTexturePack:
      return Start("Making the HD textures", [this, app](std::string& result) {
        return BuildTextures(app, done_, total_, result);
      });
    case Kind::kSave:
      return Start("Importing the save " + source.label, [this, app, source](std::string& result) {
        std::error_code ec;
        // Into saves/_import/, moved into the game's profile when it starts (PlacePendingSaves): the
        // runtime makes its profile (a random XUID, its saves' folder) on its first start, so an
        // import into whatever folder is there now can miss it (Crackdown PS5 found).
        const fs::path profile = app / "saves" / "_import";
        // The game's save files are MP3_PROGRESSION, MP3_LOADOUT, MP3_PLAYERCUSTOM and MP3_STATS,
        // and it opens no other names: the package's own name when it's one of them (as copied off
        // the 360's drive), else told from its title, else the story's progress.
        Header h;
        ReadHeader(source.path, h);
        const std::string file_upper = [&] {
          std::string n = source.path.filename().string();
          for (char& ch : n) ch = char(std::toupper(static_cast<unsigned char>(ch)));
          return n;
        }();
        const std::string title_lower = Lower(Narrow(h.name));
        std::string name = "MP3_PROGRESSION";
        for (const char* known : {"MP3_PROGRESSION", "MP3_LOADOUT", "MP3_PLAYERCUSTOM", "MP3_STATS"}) {
          if (file_upper.find(known) != std::string::npos) {
            name = known;
            break;
          }
        }
        if (file_upper.find("MP3_") == std::string::npos) {
          if (title_lower.find("loadout") != std::string::npos) name = "MP3_LOADOUT";
          else if (title_lower.find("custom") != std::string::npos) name = "MP3_PLAYERCUSTOM";
          else if (title_lower.find("stat") != std::string::npos) name = "MP3_STATS";
        }
        const fs::path title = profile / kTitleFolder;
        const fs::path dest = title / "00000001" / name, tmp = dest.string() + ".installing";
        fs::remove_all(tmp, ec);
        rex::filesystem::StfsContainerDevice package("\\Device\\Mp3Save", source.path);
        rex::filesystem::Entry* root = package.Initialize() ? package.ResolvePath("") : nullptr;
        if (!root || h.title != kTitleId) {
          result = "Couldn't read the save " + source.label + ".";
          return false;
        }
        total_ = TotalSize(root);
        std::string error;
        if (!Copier(done_).Children(root, tmp, error)) {
          fs::remove_all(tmp, ec);
          result = "Importing " + source.label + " failed: " + error;
          return false;
        }
        fs::remove_all(dest, ec);
        fs::rename(tmp, dest, ec);
        // its header, as the runtime writes one (ContentManager::WriteContentHeaderFile)
        rex::system::xam::XCONTENT_AGGREGATE_DATA data{};
        std::memset(&data, 0, sizeof(data));
        data.device_id = 1;
        data.content_type = rex::system::XContentType::kSavedGame;
        data.set_display_name(h.name);
        data.set_file_name(name);
        data.xuid = 0;  // (as the runtime writes its own: the folder says whose it is)
        data.title_id = kTitleId;
        const fs::path headers = title / "Headers" / "00000001";
        fs::create_directories(headers, ec);
        std::ofstream(headers / (name + ".header"), std::ios::binary)
            .write(reinterpret_cast<const char*>(&data), sizeof(data));
        RemoveSource(source.path);
        result = "Imported the save " + source.label + " as " + name + ". The game loads it when you PLAY.";
        return true;
      });
    case Kind::kBackup:
      return Start("Restoring " + source.label, [app, source](std::string& result) {
        std::error_code ec;
        const auto options = fs::copy_options::recursive | fs::copy_options::overwrite_existing;
        for (const char* what : {"saves", "UserData"}) {
          if (!fs::exists(source.path / what, ec)) continue;
          fs::copy(source.path / what, app / what, options, ec);
          if (ec) {
            result = "Restoring " + source.label + " failed (" + ec.message() + ").";
            return false;
          }
        }
        if (fs::exists(source.path / "menu.ini", ec))
          fs::copy_file(source.path / "menu.ini", app / "menu.ini", fs::copy_options::overwrite_existing, ec);
        result = "Restored the backup " + source.label + ".";
        return true;
      });
    default:
      return false;
  }
}

bool Job::Uninstall(const fs::path& app, Remove what) {
  const char* name = what == Remove::kDisc1    ? "disc 1"
                     : what == Remove::kDisc2  ? "disc 2"
                     : what == Remove::kUpdate ? "Title Update 9"
                     : what == Remove::kDlc    ? "the DLC"
                                               : "the HD textures";
  return Start(std::string("Removing ") + name, [app, what, name](std::string& result) {
    std::error_code ec;
    switch (what) {
      case Remove::kDisc1:
      case Remove::kDisc2: {
        // (its title update stays, for the disc's next install)
        const fs::path game = app / kDiscFolders[what == Remove::kDisc1 ? 0 : 1];
        for (fs::directory_iterator it(game, ec), end; !ec && it != end; it.increment(ec)) {
          const std::string n = Lower(it->path().filename().string());
          if (n == "default.xexp" || n == "update") continue;
          std::error_code ec2;
          fs::remove_all(it->path(), ec2);
        }
        break;
      }
      case Remove::kUpdate:
        for (const char* folder : kDiscFolders) {
          const fs::path game = app / folder;
          const fs::path xexp = FindChild(game, "default.xexp"), update = FindChild(game, "update");
          if (!xexp.empty()) fs::remove(xexp, ec);
          if (!update.empty()) fs::remove_all(update, ec);
        }
        break;
      case Remove::kDlc:
        fs::remove_all(app / "dlc", ec);
        fs::create_directories(app / "dlc", ec);
        break;
      default:
        fs::remove_all(app / "textures", ec);
        break;
    }
    result = std::string("Removed ") + name + ".";
    return !ec;
  });
}

bool Job::Backup(const fs::path& app) {
  return Start("Backing up the saves", [app](std::string& result) {
    char stamp[32];
    const std::time_t now = std::time(nullptr);
    std::strftime(stamp, sizeof(stamp), "%Y-%m-%d_%H-%M-%S", std::localtime(&now));
    const fs::path dest = Folder(app) / "backups" / (std::string("mp3-backup_") + stamp);
    std::error_code ec;
    fs::create_directories(dest, ec);
    const auto options = fs::copy_options::recursive | fs::copy_options::overwrite_existing;
    if (fs::exists(app / "saves", ec)) fs::copy(app / "saves", dest / "saves", options, ec);
    const fs::path trophies = app / "UserData" / "mp3" / "achievements";
    if (!ec && fs::exists(trophies, ec)) {
      fs::create_directories(dest / "UserData" / "mp3", ec);
      fs::copy(trophies, dest / "UserData" / "mp3" / "achievements", options, ec);
    }
    if (!ec && fs::exists(app / "menu.ini", ec)) fs::copy_file(app / "menu.ini", dest / "menu.ini", ec);
    if (ec) {
      result = "The backup failed (" + ec.message() + ").";
      return false;
    }
    std::ofstream(dest / kBackupMarker) << "Max Payne 3 RePrescribed backup: saves/, trophies (UserData/) and "
                                           "menu.ini.\nRestore it from the menu's SAVES page.\n";
    result = "Backed up to install/backups/" + dest.filename().string() + ".";
    return true;
  });
}

// The logs (<app>/logs), uploaded by the installer payload for a bug report: the result is the link
// to pass on. (Only a payload can reach the internet by name: ps5/payload/installer, logs().)
bool Job::SendLogs(const fs::path& app, const std::string& note) {
  std::string line = note;  // (a field of the job: one line)
  for (char& c : line)
    if (c == '\n' || c == '\r') c = ' ';
  return Start("Sending the logs", [this, app, line](std::string& result) {
    rex::FlushLogging();  // (this run's log so far, on the drive first)
    std::string error, link;
    switch (ByPayload(app, "kind=logs\nnote=" + line + "\n", done_, total_, error, &link, kLogsElf)) {
      case Ran::kDone: {
        if (link.empty()) {
          result = "The logs were sent, but no link came back. Try again.";
          return false;
        }
        const size_t scheme = link.find("://");
        const std::string shown = scheme == std::string::npos ? link : link.substr(scheme + 3);
        {
          std::lock_guard<std::mutex> lock(mutex_);
          logs_link_ = shown;
        }
        result = "Logs sent. Give the developer this link:  " + shown;
        return true;
      }
      case Ran::kUnavailable:
        result = "The logs can only be sent with a payload loader running (etaHEN or elfldr, port 9021). Or copy "
                 "PPSA95454/logs/ off the PS5 by FTP.";
        return false;
      default:
        result = "The logs couldn't be sent: " + error;
        return false;
    }
  });
}

void Job::Leftover(const fs::path& path) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (std::find(leftovers_.begin(), leftovers_.end(), path) == leftovers_.end()) leftovers_.push_back(path);
}

std::vector<fs::path> Job::Leftovers() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return leftovers_;
}

uint64_t Job::LeftoverBytes() const {
  uint64_t bytes = 0;
  for (const fs::path& p : Leftovers()) {
    std::error_code ec;
    const uint64_t size = fs::file_size(p, ec);
    if (!ec) bytes += size;
  }
  return bytes;
}

bool Job::DeleteLeftovers(const fs::path& app) {
  const std::vector<fs::path> files = Leftovers();
  {
    std::lock_guard<std::mutex> lock(mutex_);
    leftovers_.clear();
  }
  return Start("Deleting the installed files from install/", [files](std::string& result) {
    uint64_t freed = 0;
    for (const fs::path& f : files) {
      std::error_code ec;
      const uint64_t size = fs::file_size(f, ec);
      if (fs::remove(f, ec) && !ec) freed += size;
    }
    result = "Deleted the installed files from install/ (" + Gb(freed) + " freed).";
    return true;
  });
}

void Job::KeepLeftovers(const fs::path& app) {
  const fs::path root = Folder(app);
  std::vector<fs::path> files;
  {
    std::lock_guard<std::mutex> lock(mutex_);
    files.swap(leftovers_);
  }
  std::ofstream out(root / "kept.txt", std::ios::app);
  for (const fs::path& f : files) out << KeptLine(root, f) << "\n";
}

Job& TheJob() {
  static Job job;
  return job;
}

void PlacePendingSaves(const fs::path& saves_root, uint64_t xuid) {
  std::error_code ec;
  const fs::path pending = saves_root / "_import" / kTitleFolder;
  if (!xuid || !fs::is_directory(pending, ec)) return;
  const fs::path own = saves_root / fmt::format("{:016X}", xuid) / kTitleFolder;
  for (fs::directory_iterator it(pending / "00000001", ec), end; !ec && it != end; it.increment(ec)) {
    std::error_code ec2;
    const std::string name = it->path().filename().string();
    if (!it->is_directory(ec2) || it->path().extension() == ".installing") continue;
    // (an import replaces the profile's save of that name: that's what importing it asks for)
    const fs::path dest = own / "00000001" / name;
    fs::remove_all(dest, ec2);
    fs::create_directories(dest.parent_path(), ec2);
    fs::rename(it->path(), dest, ec2);
    if (ec2) {
      REXLOG_WARN("Max Payne 3: couldn't move the imported save {} into {} ({})", name, own.string(), ec2.message());
      continue;
    }
    const fs::path header = pending / "Headers" / "00000001" / (name + ".header");
    const fs::path dest_header = own / "Headers" / "00000001" / (name + ".header");
    fs::create_directories(dest_header.parent_path(), ec2);
    fs::remove(dest_header, ec2);
    fs::rename(header, dest_header, ec2);
    REXLOG_INFO("Max Payne 3: the imported save {} is now in {}", name, own.string());
  }
  // (nothing of it left but empty folders: gone)
  ec.clear();
  bool empty = true;
  for (fs::recursive_directory_iterator it(saves_root / "_import", ec), end; !ec && it != end; it.increment(ec))
    if (it->is_regular_file()) empty = false;
  if (!ec && empty) fs::remove_all(saves_root / "_import", ec);
}

}  // namespace mp3ps5::install

#endif  // REX_PLATFORM_PS5
