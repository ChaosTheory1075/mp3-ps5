/**
 * HD texture replacement for the Vulkan texture cache (Max Payne 3 PS5,
 * patched in by ci/patch_framework.py).
 *
 * Every time a guest texture's base level is loaded, its guest bytes are
 * hashed (XXH3-64 of the base level's data extent, seeded with the format and
 * the size). If <textures dir>/<hash as 16 hex digits>.dds exists, that DDS is
 * uploaded once into an image of its own and the texture's views come from it
 * instead; the guest image is still loaded as always, so nothing else in the
 * cache changes. When the guest memory changes, the reload hashes again.
 *
 * The DDS files are read on a thread of their own: until one is in, the
 * texture draws as the guest's, and the binding is refreshed when it arrives
 * (reading them on the GPU thread held up frames whenever new ones came in).
 *
 * Only 2D, non-array, unsigned DXT1 / DXT2_3 / DXT4_5 / DXN / 8_8_8_8 textures
 * whose host format needs no swizzle of its own are replaced: their data's
 * channels are the replacement's (the converter, tools/mp3_texture_pack.py,
 * writes them so).
 */

#include <algorithm>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <iterator>
#include <list>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <xxhash.h>

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/mp3_perf.h>
#include <rex/graphics/vulkan/command_processor.h>
#include <rex/graphics/vulkan/deferred_command_buffer.h>
#include <rex/graphics/vulkan/texture_cache.h>
#include <rex/system/kernel_state.h>
#include <rex/ui/vulkan/util.h>

REXCVAR_DEFINE_BOOL(hd_textures, true, "GPU/HD Textures",
                    "Draw the textures folder's DDS files in place of the guest textures they replace");
REXCVAR_DEFINE_STRING(hd_textures_path, "/app0/textures", "GPU/HD Textures",
                      "The folder of replacement textures (<hash>.dds)");
REXCVAR_DEFINE_INT32(hd_textures_budget_mb, 3072, "GPU/HD Textures",
                     "GPU memory for replacement textures before unused ones are let go");
REXCVAR_DEFINE_BOOL(hd_textures_log_misses, false, "GPU/HD Textures",
                    "Log the hash of every replaceable texture loaded without a replacement");

namespace rex::graphics::vulkan {

namespace fs = std::filesystem;

struct HdDds {
  VkFormat format = VK_FORMAT_UNDEFINED;
  uint32_t width = 0, height = 0, mips = 1;
  bool block_compressed = true;
  uint32_t bytes_per_block = 0;  // (per texel when not block compressed)
  std::vector<uint8_t> data;     // every mip, smallest last
};

struct HdReplacement {
  uint64_t hash = 0;
  VkImage image = VK_NULL_HANDLE;
  VmaAllocation allocation = VK_NULL_HANDLE;
  VkFormat format = VK_FORMAT_UNDEFINED;
  uint32_t width = 0, height = 0, mips = 0;
  uint64_t bytes = 0;
  uint64_t last_used_submission = 0;
  std::unordered_map<uint32_t, VkImageView> views;
};

struct HdReplacer {
  fs::path dir;
  std::unordered_set<uint64_t> available;
  std::unordered_set<uint64_t> failed;
  std::unordered_map<uint64_t, std::shared_ptr<HdReplacement>> loaded;
  std::list<uint64_t> lru;  // (front: used most recently)
  uint64_t loaded_bytes = 0;
  struct Staging {
    VkBuffer buffer;
    VmaAllocation allocation;
    uint64_t submission;
  };
  std::vector<Staging> staging;
  std::vector<std::shared_ptr<HdReplacement>> retired;  // (destroyed once their submission is done)
  uint64_t replaced = 0;
  // the file reader: hashes asked for, files read (empty data: unreadable)
  std::mutex mutex;
  std::condition_variable cv;
  std::vector<uint64_t> requests;
  std::vector<std::pair<uint64_t, HdDds>> done;
  std::unordered_set<uint64_t> pending;  // (GPU thread only)
  std::thread reader;
  // report counters: loads seen, not replaceable (by reason), hashed, matched
  uint64_t seen = 0, skip_kind = 0, skip_swizzle = 0, skip_size = 0, skip_dynamic = 0, hashed = 0, matched = 0,
           misses_logged = 0;
};

namespace {

// ---- DDS ----

uint32_t Read32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }

bool FormatFromDxgi(uint32_t dxgi, HdDds& d) {
  switch (dxgi) {
    case 71: case 72: d.format = VK_FORMAT_BC1_RGBA_UNORM_BLOCK; d.bytes_per_block = 8; return true;
    case 74: case 75: d.format = VK_FORMAT_BC2_UNORM_BLOCK; d.bytes_per_block = 16; return true;
    case 77: case 78: d.format = VK_FORMAT_BC3_UNORM_BLOCK; d.bytes_per_block = 16; return true;
    case 80: d.format = VK_FORMAT_BC4_UNORM_BLOCK; d.bytes_per_block = 8; return true;
    case 83: d.format = VK_FORMAT_BC5_UNORM_BLOCK; d.bytes_per_block = 16; return true;
    case 98: case 99: d.format = VK_FORMAT_BC7_UNORM_BLOCK; d.bytes_per_block = 16; return true;
    case 28: case 29:
      d.format = VK_FORMAT_R8G8B8A8_UNORM, d.bytes_per_block = 4, d.block_compressed = false;
      return true;
    case 87: case 91:
      d.format = VK_FORMAT_B8G8R8A8_UNORM, d.bytes_per_block = 4, d.block_compressed = false;
      return true;
    default: return false;
  }
}

bool LoadDds(const fs::path& file, HdDds& d) {
  std::FILE* f = std::fopen(file.string().c_str(), "rb");
  if (!f) return false;
  std::vector<uint8_t> all;
  std::fseek(f, 0, SEEK_END);
  const long size = std::ftell(f);
  std::fseek(f, 0, SEEK_SET);
  if (size > 128) {
    all.resize(size_t(size));
    if (std::fread(all.data(), 1, all.size(), f) != all.size()) all.clear();
  }
  std::fclose(f);
  if (all.size() <= 128 || std::memcmp(all.data(), "DDS ", 4) != 0) return false;
  const uint8_t* h = all.data() + 4;
  d.height = Read32(h + 8);
  d.width = Read32(h + 12);
  d.mips = std::max(Read32(h + 24), 1u);
  const uint32_t pf_flags = Read32(h + 76), fourcc = Read32(h + 80);
  size_t offset = 128;
  if ((pf_flags & 0x4) && fourcc == 0x30315844) {  // "DX10"
    if (all.size() < 148 || !FormatFromDxgi(Read32(all.data() + 128), d)) return false;
    offset = 148;
  } else if (pf_flags & 0x4) {
    if (fourcc == 0x31545844) FormatFromDxgi(71, d);       // DXT1
    else if (fourcc == 0x33545844) FormatFromDxgi(74, d);  // DXT3
    else if (fourcc == 0x35545844) FormatFromDxgi(77, d);  // DXT5
    else if (fourcc == 0x32495441) FormatFromDxgi(83, d);  // ATI2
    else return false;
  } else if ((pf_flags & 0x40) && Read32(h + 84) == 32) {
    // 32-bit RGB(A): BGRA or RGBA by the red mask
    FormatFromDxgi(Read32(h + 88) == 0x00FF0000u ? 87 : 28, d);
  } else {
    return false;
  }
  if (!d.width || !d.height || d.width > 16384 || d.height > 16384) return false;
  // as many mips as the file holds
  size_t need = 0;
  uint32_t mips = 0;
  for (uint32_t m = 0; m < d.mips; ++m) {
    const uint32_t w = std::max(d.width >> m, 1u), h2 = std::max(d.height >> m, 1u);
    const size_t level = d.block_compressed ? size_t((w + 3) / 4) * ((h2 + 3) / 4) * d.bytes_per_block
                                            : size_t(w) * h2 * d.bytes_per_block;
    if (offset + need + level > all.size()) break;
    need += level;
    ++mips;
  }
  if (!mips) return false;
  d.mips = mips;
  d.data.assign(all.begin() + offset, all.begin() + offset + need);
  return true;
}

// The file reader thread: reads the DDS files the GPU thread asks for.
void HdReaderMain(HdReplacer* hd) {
  for (;;) {
    uint64_t hash;
    {
      std::unique_lock<std::mutex> lock(hd->mutex);
      hd->cv.wait(lock, [hd] { return !hd->requests.empty(); });
      hash = hd->requests.front();
      hd->requests.erase(hd->requests.begin());
    }
    char name[24];
    std::snprintf(name, sizeof(name), "%016llx.dds", static_cast<unsigned long long>(hash));
    fs::path file = hd->dir / name;
    std::error_code ec;
    if (!fs::exists(file, ec)) {
      // (in a subfolder: the converter writes <first two digits>/<hash>.dds)
      file = hd->dir / std::string(name, 2) / name;
    }
    HdDds dds;
    if (!LoadDds(file, dds)) {
      REXGPU_WARN("HD textures: can't read {}", file.string());
      dds = HdDds();
    }
    std::lock_guard<std::mutex> lock(hd->mutex);
    hd->done.emplace_back(hash, std::move(dds));
  }
}

}  // namespace

HdReplacer* VulkanTextureCache::Hd() const {
  if (hd_ || hd_disabled_) return hd_;
  hd_disabled_ = true;
  if (!REXCVAR_GET(hd_textures)) {
    REXGPU_INFO("HD textures: off");
    return nullptr;
  }
  const fs::path dir = REXCVAR_GET(hd_textures_path);
  std::error_code ec;
  if (!fs::is_directory(dir, ec)) {
    REXGPU_INFO("HD textures: no folder {}", dir.string());
    return nullptr;
  }
  auto* hd = new HdReplacer();
  hd->dir = dir;
  for (const auto& e : fs::recursive_directory_iterator(dir, ec)) {
    if (!e.is_regular_file(ec)) continue;
    const std::string name = e.path().filename().string();
    if (name.size() != 20 || e.path().extension() != ".dds") continue;
    char* end = nullptr;
    const uint64_t hash = std::strtoull(name.substr(0, 16).c_str(), &end, 16);
    if (end && *end == '\0') hd->available.insert(hash);
  }
  // (files in subfolders are found by name: keep a flat index of where they are)
  REXGPU_INFO("HD textures: {} replacement textures in {}", hd->available.size(), dir.string());
  if (hd->available.empty() && !REXCVAR_GET(hd_textures_log_misses)) {
    delete hd;
    return nullptr;
  }
  hd->reader = std::thread(HdReaderMain, hd);
  hd->reader.detach();
  hd_ = hd;
  hd_disabled_ = false;
  return hd_;
}

void VulkanTextureCache::HdBeginSubmission() {
  HdReplacer* hd = hd_;
  if (!hd) return;
  const uint64_t completed = command_processor_.GetCompletedSubmission();
  auto& st = hd->staging;
  for (size_t i = 0; i < st.size();) {
    if (st[i].submission <= completed) {
      vmaDestroyBuffer(vma_allocator_, st[i].buffer, st[i].allocation);
      st[i] = st.back();
      st.pop_back();
    } else {
      ++i;
    }
  }
  const ui::vulkan::VulkanDevice* vulkan_device = command_processor_.GetVulkanDevice();
  const auto& dfn = vulkan_device->functions();
  auto& retired = hd->retired;
  for (size_t i = 0; i < retired.size();) {
    HdReplacement& r = *retired[i];
    if (r.last_used_submission <= completed) {
      for (const auto& v : r.views) dfn.vkDestroyImageView(vulkan_device->device(), v.second, nullptr);
      vmaDestroyImage(vma_allocator_, r.image, r.allocation);
      retired[i] = retired.back();
      retired.pop_back();
    } else {
      ++i;
    }
  }
  // over the budget: let go of the least recently used ones no texture holds
  const uint64_t budget = uint64_t(std::max(REXCVAR_GET(hd_textures_budget_mb), 256)) << 20;
  for (auto it = hd->lru.end(); hd->loaded_bytes > budget && it != hd->lru.begin();) {
    --it;
    auto found = hd->loaded.find(*it);
    if (found == hd->loaded.end() || found->second.use_count() > 1) continue;
    hd->loaded_bytes -= found->second->bytes;
    hd->retired.push_back(std::move(found->second));
    hd->loaded.erase(found);
    it = hd->lru.erase(it);
  }
}

std::shared_ptr<HdReplacement> VulkanTextureCache::HdLoad(uint64_t hash) {
  HdReplacer* hd = hd_;
  if (auto it = hd->loaded.find(hash); it != hd->loaded.end()) {
    hd->lru.remove(hash);
    hd->lru.push_front(hash);
    return it->second;
  }
  if (hd->failed.count(hash) || !hd->pending.insert(hash).second) return nullptr;
  {
    std::lock_guard<std::mutex> lock(hd->mutex);
    hd->requests.push_back(hash);
  }
  hd->cv.notify_one();
  return nullptr;
}

void VulkanTextureCache::HdPoll() {
  HdReplacer* hd = hd_;
  if (!hd || hd->pending.empty()) return;
  std::vector<std::pair<uint64_t, HdDds>> done;
  {
    std::lock_guard<std::mutex> lock(hd->mutex);
    if (hd->done.empty()) return;
    // (a few at a time, so a burst of new ones doesn't hold up a frame)
    size_t n = 0, bytes = 0;
    while (n < hd->done.size() && n < 16 && bytes < (48u << 20)) bytes += hd->done[n++].second.data.size();
    done.assign(std::make_move_iterator(hd->done.begin()), std::make_move_iterator(hd->done.begin() + n));
    hd->done.erase(hd->done.begin(), hd->done.begin() + n);
  }
  mp3_perf::Scope perf(mp3_perf::kHd);
  for (auto& [hash, dds] : done) {
    hd->pending.erase(hash);
    if (dds.data.empty() || !HdUpload(hash, dds)) hd->failed.insert(hash);
  }
  // (the textures waiting for these take them in GetView, see HdView)
  ResetTextureBindings();
}

std::shared_ptr<HdReplacement> VulkanTextureCache::HdUpload(uint64_t hash, HdDds& dds) {
  HdReplacer* hd = hd_;
  char name[24];
  std::snprintf(name, sizeof(name), "%016llx.dds", static_cast<unsigned long long>(hash));
  auto r = std::make_shared<HdReplacement>();
  r->hash = hash;
  r->format = dds.format;
  r->width = dds.width;
  r->height = dds.height;
  r->mips = dds.mips;

  VkImageCreateInfo image_info = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
  image_info.imageType = VK_IMAGE_TYPE_2D;
  image_info.format = dds.format;
  image_info.extent = {dds.width, dds.height, 1};
  image_info.mipLevels = dds.mips;
  image_info.arrayLayers = 1;
  image_info.samples = VK_SAMPLE_COUNT_1_BIT;
  image_info.tiling = VK_IMAGE_TILING_OPTIMAL;
  image_info.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
  image_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
  image_info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
  VmaAllocationCreateInfo image_alloc = {};
  image_alloc.usage = VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE;
  if (vmaCreateImage(vma_allocator_, &image_info, &image_alloc, &r->image, &r->allocation, nullptr) != VK_SUCCESS) {
    REXGPU_WARN("HD textures: no image for {} ({}x{})", name, dds.width, dds.height);
    hd->failed.insert(hash);
    return nullptr;
  }
  VmaAllocationInfo image_alloc_info;
  vmaGetAllocationInfo(vma_allocator_, r->allocation, &image_alloc_info);
  r->bytes = image_alloc_info.size;

  VkBufferCreateInfo buffer_info = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
  buffer_info.size = dds.data.size();
  buffer_info.usage = VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
  buffer_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
  VmaAllocationCreateInfo buffer_alloc = {};
  buffer_alloc.usage = VMA_MEMORY_USAGE_AUTO;
  buffer_alloc.flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_SEQUENTIAL_WRITE_BIT | VMA_ALLOCATION_CREATE_MAPPED_BIT;
  HdReplacer::Staging staging{};
  VmaAllocationInfo staging_info;
  if (vmaCreateBuffer(vma_allocator_, &buffer_info, &buffer_alloc, &staging.buffer, &staging.allocation,
                      &staging_info) != VK_SUCCESS ||
      !staging_info.pMappedData) {
    vmaDestroyImage(vma_allocator_, r->image, r->allocation);
    hd->failed.insert(hash);
    return nullptr;
  }
  std::memcpy(staging_info.pMappedData, dds.data.data(), dds.data.size());
  vmaFlushAllocation(vma_allocator_, staging.allocation, 0, VK_WHOLE_SIZE);
  staging.submission = command_processor_.GetCurrentSubmission();
  hd->staging.push_back(staging);

  // UNDEFINED -> transfer, copy every mip, -> sampled (as the null images)
  VkImageSubresourceRange range = ui::vulkan::util::InitializeSubresourceRange();
  command_processor_.PushImageMemoryBarrier(r->image, range, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                                            VK_PIPELINE_STAGE_TRANSFER_BIT, 0, VK_ACCESS_TRANSFER_WRITE_BIT,
                                            VK_IMAGE_LAYOUT_UNDEFINED, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                            VK_QUEUE_FAMILY_IGNORED, VK_QUEUE_FAMILY_IGNORED, false);
  command_processor_.SubmitBarriers(true);
  DeferredCommandBuffer& command_buffer = command_processor_.deferred_command_buffer();
  VkBufferImageCopy* regions = command_buffer.CmdCopyBufferToImageEmplace(
      staging.buffer, r->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, dds.mips);
  VkDeviceSize offset = 0;
  for (uint32_t m = 0; m < dds.mips; ++m) {
    const uint32_t w = std::max(dds.width >> m, 1u), h = std::max(dds.height >> m, 1u);
    VkBufferImageCopy& c = regions[m];
    c = {};
    c.bufferOffset = offset;
    c.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
    c.imageSubresource.mipLevel = m;
    c.imageSubresource.layerCount = 1;
    c.imageExtent = {w, h, 1};
    offset += dds.block_compressed ? VkDeviceSize((w + 3) / 4) * ((h + 3) / 4) * dds.bytes_per_block
                                   : VkDeviceSize(w) * h * dds.bytes_per_block;
  }
  command_processor_.PushImageMemoryBarrier(r->image, range, VK_PIPELINE_STAGE_TRANSFER_BIT,
                                            guest_shader_pipeline_stages_, VK_ACCESS_TRANSFER_WRITE_BIT,
                                            VK_ACCESS_SHADER_READ_BIT, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
                                            VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL, VK_QUEUE_FAMILY_IGNORED,
                                            VK_QUEUE_FAMILY_IGNORED, false);

  hd->loaded.emplace(hash, r);
  hd->lru.push_front(hash);
  hd->loaded_bytes += r->bytes;
  if ((++hd->replaced % 100) == 1)
    REXGPU_INFO("HD textures: {} loaded ({} MB), latest {} {}x{}", hd->loaded.size(), hd->loaded_bytes >> 20, name,
                dds.width, dds.height);
  return r;
}

void VulkanTextureCache::HdAfterLoad(VulkanTexture& texture, bool load_base) {
  if (!load_base) return;
  mp3_perf::Scope perf(mp3_perf::kHd);
  texture.hd_pending = 0;
  HdReplacer* hd = Hd();
  if (!hd) {
    texture.hd_replacement.reset();
    return;
  }
  const TextureKey key = texture.key();
  if ((++hd->seen % 20000) == 1)
    REXGPU_INFO("HD textures report: {} loads, {} other kind, {} other swizzle, {} bad size, {} rewritten often, "
                "{} hashed, {} matched",
                hd->seen, hd->skip_kind, hd->skip_swizzle, hd->skip_size, hd->skip_dynamic, hd->hashed, hd->matched);
  bool replaceable = key.dimension == xenos::DataDimension::k2DOrStacked && key.GetDepthOrArraySize() == 1 &&
                     !key.scaled_resolve && !key.signed_separate;
  switch (key.format) {
    case xenos::TextureFormat::k_DXT1:
    case xenos::TextureFormat::k_DXT2_3:
    case xenos::TextureFormat::k_DXT4_5:
    case xenos::TextureFormat::k_DXN:
    case xenos::TextureFormat::k_8_8_8_8:
      break;
    default:
      replaceable = false;
  }
  if (!replaceable || GetHostFormatSwizzle(key) != xenos::XE_GPU_TEXTURE_SWIZZLE_RGBA) {
    ++(replaceable ? hd->skip_swizzle : hd->skip_kind);
    texture.hd_replacement.reset();
    return;
  }
  const uint32_t size = texture.GetGuestBaseSize();
  const uint32_t address = key.base_page << 12;
  if (!size || uint64_t(address) + size > SharedMemory::kBufferSize) {
    ++hd->skip_size;
    texture.hd_replacement.reset();
    return;
  }
  // Textures the game rewrites every frame or so (render targets, video,
  // dynamic maps) are never in the pack: past a few quick reloads in a row
  // they aren't hashed (that was ~2.5 ms a frame in gameplay).
  const uint64_t submission = command_processor_.GetCurrentSubmission();
  if (texture.hd_last_load && submission - texture.hd_last_load <= 30) {
    if (texture.hd_fast_reloads < 255) ++texture.hd_fast_reloads;
  } else {
    texture.hd_fast_reloads = 0;
  }
  texture.hd_last_load = submission;
  if (texture.hd_fast_reloads >= 4) {
    ++hd->skip_dynamic;
    texture.hd_replacement.reset();
    return;
  }
  const auto* data = REX_KERNEL_MEMORY()->TranslatePhysical<const uint8_t*>(address);
  // (the converter, tools/mp3_texture_pack.py, names the files by the same hash)
  const uint64_t seed = uint64_t(key.format) | uint64_t(key.GetWidth()) << 8 | uint64_t(key.GetHeight()) << 24 |
                        uint64_t(key.tiled) << 40;
  const uint64_t hash = XXH3_64bits_withSeed(data, size, seed);
  ++hd->hashed;
  if (texture.hd_replacement && texture.hd_replacement->hash == hash) return;
  if (!hd->available.count(hash)) {
    // (the first misses of 32x32 and up, to compare with tools/mp3_texture_pack.py)
    if (hd->misses_logged < 300 && key.GetWidth() >= 32 && key.GetHeight() >= 32) {
      ++hd->misses_logged;
      REXGPU_INFO("HD textures: miss {:016x} format {} {}x{} tiled {} pitch {} packed {} mips {} base {:08X} size {:X} "
                  "head {:016x}",
                  hash, uint32_t(key.format), key.GetWidth(), key.GetHeight(), uint32_t(key.tiled),
                  uint32_t(key.pitch) << 5, uint32_t(key.packed_mips), uint32_t(key.mip_max_level), address, size,
                  XXH3_64bits(data, std::min<uint32_t>(size, 4096)));
    } else if (REXCVAR_GET(hd_textures_log_misses))
      REXGPU_INFO("HD textures: miss {:016x} format {} {}x{} tiled {}", hash, uint32_t(key.format), key.GetWidth(),
                  key.GetHeight(), uint32_t(key.tiled));
    texture.hd_replacement.reset();
    return;
  }
  ++hd->matched;
  texture.hd_replacement = HdLoad(hash);
  if (!texture.hd_replacement) texture.hd_pending = hash;
}

VkImageView VulkanTextureCache::HdView(VulkanTexture& texture, bool is_signed, uint32_t host_swizzle,
                                       bool is_array) const {
  if (texture.hd_pending && hd_) {
    // (read in since this texture was loaded)
    if (auto it = hd_->loaded.find(texture.hd_pending); it != hd_->loaded.end()) {
      texture.hd_replacement = it->second;
      texture.hd_pending = 0;
    } else if (hd_->failed.count(texture.hd_pending)) {
      texture.hd_pending = 0;
    }
  }
  HdReplacement* r = texture.hd_replacement.get();
  if (!r || is_signed) return VK_NULL_HANDLE;
  r->last_used_submission = command_processor_.GetCurrentSubmission();
  const ui::vulkan::VulkanDevice* vulkan_device = command_processor_.GetVulkanDevice();
  if (!vulkan_device->properties().imageViewFormatSwizzle) host_swizzle = xenos::XE_GPU_TEXTURE_SWIZZLE_RGBA;
  const uint32_t view_key = (host_swizzle & 0xFFF) | (is_array ? 0x1000u : 0u);
  if (auto it = r->views.find(view_key); it != r->views.end()) return it->second;
  VkImageViewCreateInfo info = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
  info.image = r->image;
  info.format = r->format;
  info.viewType = is_array ? VK_IMAGE_VIEW_TYPE_2D_ARRAY : VK_IMAGE_VIEW_TYPE_2D;
  info.components.r = VulkanTexture::GetComponentSwizzle(host_swizzle, 0);
  info.components.g = VulkanTexture::GetComponentSwizzle(host_swizzle, 1);
  info.components.b = VulkanTexture::GetComponentSwizzle(host_swizzle, 2);
  info.components.a = VulkanTexture::GetComponentSwizzle(host_swizzle, 3);
  info.subresourceRange = ui::vulkan::util::InitializeSubresourceRange();
  info.subresourceRange.layerCount = 1;
  VkImageView view;
  if (vulkan_device->functions().vkCreateImageView(vulkan_device->device(), &info, nullptr, &view) != VK_SUCCESS)
    return VK_NULL_HANDLE;
  r->views.emplace(view_key, view);
  return view;
}

}  // namespace rex::graphics::vulkan
