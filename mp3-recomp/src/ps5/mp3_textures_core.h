// Max Payne 3 RePrescribed - the HD textures made from the user's own PC files, following the
// list the app carries (hub/mp3-textures.txt, tools/mp3_texture_manifest.py): where each HD
// texture's pixels are in the Nexus texture pack's (and the PC game's) archives. Only those
// archives' tables of contents are encrypted, so each texture is read straight from its offset,
// inflated, checked against its XXH3 and written as <out>/<xx>/<hash>.dds - no key needed.
//
// Shared by the app (mp3_textures_install.cpp, when there's no payload loader) and the installer
// payload (installer.cpp, kind=textures: written at the drive's full speed). Header only; the file
// including it includes gta4_install_io.h (the writer), zlib.h and xxhash.h first.
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

namespace mp3tex {

namespace fs = std::filesystem;

struct Step {
  uint64_t offset = 0, size = 0;
  char kind = '0';  // '0' stored, '1' deflated (an archive in an archive), 'r' the resource
};
struct Tex {
  std::string hash;
  uint32_t width = 0, height = 0, mips = 1;
  char fourcc[4] = {};
  uint64_t offset = 0, length = 0, xxh = 0;
};
struct Res {
  uint32_t source = 0;
  std::vector<Step> steps;
  std::vector<Tex> textures;
};
struct Src {
  uint32_t folder = 0;
  uint64_t size = 0;
  std::string rel;               // as in the list
  std::vector<fs::path> found;   // the user's copies (by name and size), the likeliest first
};
struct Manifest {
  std::vector<Src> sources;
  std::vector<Res> resources;
  size_t textures = 0;
};

inline std::string LowerPath(std::string s) {
  for (char& c : s) c = c == '\\' ? '/' : char(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

inline bool LoadManifest(const fs::path& file, Manifest& m) {
  std::ifstream in(file);
  if (!in) return false;
  std::string line;
  while (std::getline(in, line)) {
    if (line.size() < 2 || line[0] == '#') continue;
    std::istringstream ls(line.substr(2));
    if (line[0] == 'S') {
      Src s;
      std::string idx;
      ls >> idx >> s.folder >> std::hex >> s.size;
      std::getline(ls >> std::ws, s.rel);
      m.sources.push_back(s);
    } else if (line[0] == 'R') {
      Res r;
      std::string idx, steps;
      ls >> idx >> std::hex >> r.source >> steps;
      std::istringstream ss(steps);
      std::string one;
      while (std::getline(ss, one, ';')) {
        Step st;
        unsigned long long o = 0, z = 0;
        char kind = 0;
        if (std::sscanf(one.c_str(), "%llx:%llx:%c", &o, &z, &kind) != 3) return false;
        st.offset = o, st.size = z, st.kind = kind;
        r.steps.push_back(st);
      }
      m.resources.push_back(std::move(r));
    } else if (line[0] == 'T') {
      Tex t;
      std::string res, fourcc, xxh;
      ls >> std::hex >> res >> t.hash >> std::dec >> t.width >> t.height >> t.mips >> fourcc >> std::hex >> t.offset >>
          t.length >> xxh;
      t.xxh = std::stoull(xxh, nullptr, 16);
      std::memcpy(t.fourcc, fourcc.data(), std::min<size_t>(4, fourcc.size()));
      const size_t ri = std::stoul(res, nullptr, 16);
      if (ri >= m.resources.size()) return false;
      m.resources[ri].textures.push_back(t);
      ++m.textures;
    }
  }
  return !m.resources.empty();
}

// Inflates `in` (zlib with its header when `header`, else raw deflate) into `out`, `want` bytes at most
// (0: as much as there is).
inline bool Inflate(const uint8_t* in, size_t size, bool header, std::vector<uint8_t>& out, size_t want) {
  z_stream z{};
  if (inflateInit2(&z, header ? 15 : -15) != Z_OK) return false;
  out.assign(want ? want : std::max<size_t>(size * 4, 1 << 16), 0);
  z.next_in = const_cast<Bytef*>(in);
  z.avail_in = uInt(size);
  size_t done = 0;
  int r = Z_OK;
  while (r == Z_OK) {
    if (done == out.size()) {
      if (want) break;
      out.resize(out.size() * 2);
    }
    z.next_out = out.data() + done;
    z.avail_out = uInt(out.size() - done);
    r = inflate(&z, Z_NO_FLUSH);
    done = out.size() - z.avail_out;
  }
  inflateEnd(&z);
  out.resize(done);
  return r == Z_STREAM_END || (want && done == want);
}

inline bool ReadAt(std::ifstream& f, uint64_t offset, uint64_t size, std::vector<uint8_t>& out) {
  out.resize(size_t(size));
  f.clear();
  f.seekg(std::streamoff(offset));
  return bool(f.read(reinterpret_cast<char*>(out.data()), std::streamsize(size)));
}

// A resource ("RSC", version, flags, then its data compressed): its segments, inflated; the
// physical one from `physical_at`.
inline bool ResourcePhysical(const std::vector<uint8_t>& raw, std::vector<uint8_t>& out, size_t& physical_at) {
  if (raw.size() < 13 || std::memcmp(raw.data(), "RSC", 3) != 0) return false;
  uint32_t flags = uint32_t(raw[8]) | uint32_t(raw[9]) << 8 | uint32_t(raw[10]) << 16 | uint32_t(raw[11]) << 24;
  const uint32_t be = uint32_t(raw[8]) << 24 | uint32_t(raw[9]) << 16 | uint32_t(raw[10]) << 8 | uint32_t(raw[11]);
  if ((flags & 0x7FF) == 0 && be) flags = be;
  const uint64_t virt = uint64_t(flags & 0x7FF) << (((flags >> 11) & 0xF) + 8);
  const uint64_t phys = uint64_t((flags >> 15) & 0x7FF) << (((flags >> 26) & 0xF) + 8);
  const uint8_t* body = raw.data() + 12;
  if (!Inflate(body, raw.size() - 12, body[0] == 0x78, out, size_t(virt + phys))) return false;
  physical_at = size_t(virt);
  return out.size() >= virt;
}

inline bool WriteDds(const fs::path& path, const Tex& t, const uint8_t* data, gta4::install::io::AlignedBuffer& gather) {
  // (tools/dds.py: a legacy FourCC header)
  uint8_t h[128] = {};
  auto u32 = [&](size_t at, uint32_t v) { std::memcpy(h + at, &v, 4); };
  std::memcpy(h, "DDS ", 4);
  const bool bc = !std::memcmp(t.fourcc, "DXT", 3) || !std::memcmp(t.fourcc, "ATI2", 4);
  const uint32_t bpb = !std::memcmp(t.fourcc, "DXT1", 4) ? 8 : 16;
  u32(4, 124);
  u32(8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | (bc ? 0x80000 : 0x8));
  u32(12, t.height);
  u32(16, t.width);
  u32(20, bc ? std::max(1u, (t.width + 3) / 4) * std::max(1u, (t.height + 3) / 4) * bpb : t.width * 4);
  u32(28, t.mips);
  u32(76, 32);
  if (bc) {
    u32(80, 0x4);
    std::memcpy(h + 84, t.fourcc, 4);
  } else {
    u32(80, 0x41);
    u32(88, 32);
    u32(92, 0x000000FF), u32(96, 0x0000FF00), u32(100, 0x00FF0000), u32(104, 0xFF000000);
  }
  u32(108, 0x1000 | (t.mips > 1 ? 0x400008 : 0));
  gta4::install::io::OutFile out;
  if (!out.Open(path, &gather, size_t(128 + t.length))) return false;
  const bool ok = out.Write(h, sizeof(h)) && out.Write(data, size_t(t.length));
  return out.Close() && ok;
}

// Reads one of the list's resources out of a file: the physical segment in `inflated` (from
// `physical_at`). Keeps the last archive it inflated out of another.
class Reader {
 public:
  bool Resource(const fs::path& file, const Res& r, std::vector<uint8_t>& inflated, size_t& physical_at) {
    if (file != open_path_) {
      file_.close();
      file_.clear();
      file_.open(file, std::ios::binary);
      open_path_ = file;
      cached_key_.clear();
    }
    if (!file_ || r.steps.empty()) return false;
    std::string key = file.string();
    uint64_t base = 0;
    bool in_memory = false;
    for (size_t si = 0; si + 1 < r.steps.size(); ++si) {
      const Step& st = r.steps[si];
      key += ":" + std::to_string(st.offset);
      if (!in_memory && st.kind == '0') {
        base += st.offset;
        continue;
      }
      if (key == cached_key_) {
        in_memory = true;
        continue;
      }
      std::vector<uint8_t> part;
      if (in_memory) {
        if (st.offset + st.size > cached_.size()) return false;
        part.assign(cached_.begin() + std::ptrdiff_t(st.offset), cached_.begin() + std::ptrdiff_t(st.offset + st.size));
      } else if (!ReadAt(file_, base + st.offset, st.size, part)) {
        return false;
      }
      if (st.kind == '1') {
        std::vector<uint8_t> out;
        if (!Inflate(part.data(), part.size(), false, out, 0)) return false;
        part.swap(out);
      }
      cached_.swap(part);
      cached_key_ = key;
      in_memory = true;
    }
    const Step& last = r.steps.back();
    if (in_memory) {
      if (last.offset + last.size > cached_.size()) return false;
      raw_.assign(cached_.begin() + std::ptrdiff_t(last.offset), cached_.begin() + std::ptrdiff_t(last.offset + last.size));
    } else if (!ReadAt(file_, base + last.offset, last.size, raw_)) {
      return false;
    }
    return ResourcePhysical(raw_, inflated, physical_at);
  }
  void Close() { file_.close(), open_path_.clear(); }

 private:
  std::ifstream file_;
  fs::path open_path_;
  std::string cached_key_;
  std::vector<uint8_t> cached_, raw_;
};

// The bytes of the textures whose files were found.
inline uint64_t UsableBytes(const Manifest& m, size_t* count = nullptr) {
  uint64_t bytes = 0;
  size_t n = 0;
  for (const Res& r : m.resources)
    if (r.source < m.sources.size() && !m.sources[r.source].found.empty())
      for (const Tex& t : r.textures) bytes += t.length, ++n;
  if (count) *count = n;
  return bytes;
}

// Every texture whose file was found into <out>/<xx>/<hash>.dds. Each file's copies are tried until
// one's textures match their hashes (the Nexus pack's patches have the sizes of the files they
// replace). `done` counts the bytes gone through; false: a write failed or `cancel` was set.
inline bool Build(Manifest& m, const fs::path& out, std::atomic<uint64_t>& done, const std::atomic<bool>* cancel,
                  size_t& written, size_t& bad, std::string& error) {
  written = bad = 0;
  std::error_code ec;
  fs::create_directories(out, ec);
  // (by file: an archive inflated once serves the resources after it)
  std::vector<size_t> order(m.resources.size());
  for (size_t i = 0; i < order.size(); ++i) order[i] = i;
  std::stable_sort(order.begin(), order.end(),
                   [&](size_t a, size_t b) { return m.resources[a].source < m.resources[b].source; });
  std::vector<size_t> preferred(m.sources.size(), 0);  // (the copy of each file that matched)
  gta4::install::io::AlignedBuffer gather;
  Reader reader;
  std::vector<uint8_t> inflated, best_inflated;
  auto matches = [&](const Res& r, const std::vector<uint8_t>& data, size_t physical_at) {
    size_t n = 0;
    for (const Tex& t : r.textures) {
      const size_t at = physical_at + size_t(t.offset);
      n += at + t.length <= data.size() && XXH3_64bits(data.data() + at, size_t(t.length)) == t.xxh;
    }
    return n;
  };
  for (size_t ri : order) {
    if (cancel && cancel->load()) {
      error = "Stopped.";
      return false;
    }
    const Res& r = m.resources[ri];
    if (r.source >= m.sources.size()) continue;
    const Src& src = m.sources[r.source];
    if (src.found.empty()) continue;
    size_t physical_at = 0, best = 0, best_at = 0;
    bool have = false;
    for (size_t k = 0; k < src.found.size(); ++k) {
      const size_t ci = (preferred[r.source] + k) % src.found.size();
      if (!reader.Resource(src.found[ci], r, inflated, physical_at)) continue;
      const size_t n = matches(r, inflated, physical_at);
      if (n > best || !have) {
        best = n, best_at = physical_at, have = true;
        best_inflated.swap(inflated);
        preferred[r.source] = ci;
      }
      if (best == r.textures.size()) break;
    }
    for (const Tex& t : r.textures) {
      done.fetch_add(t.length, std::memory_order_relaxed);
      const size_t at = best_at + size_t(t.offset);
      if (!have || at + t.length > best_inflated.size() ||
          XXH3_64bits(best_inflated.data() + at, size_t(t.length)) != t.xxh) {
        ++bad;
        continue;
      }
      const fs::path dir = out / t.hash.substr(0, 2);
      fs::create_directories(dir, ec);
      if (!WriteDds(dir / (t.hash + ".dds"), t, best_inflated.data() + at, gather)) {
        reader.Close();
        error = "Writing the HD textures failed (is the drive full?).";
        return false;
      }
      ++written;
    }
  }
  reader.Close();
  return true;
}

}  // namespace mp3tex
