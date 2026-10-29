#!/usr/bin/env python3
# [mp3] Ported from crackdown-ps5's ci/fw_perf_fixes.py (same framework, same
# generic Xenos/Vulkan path): its GPU command thread work, which took Crackdown
# from 20-40 to 40-60 FPS. Left out: 0013 (patch_framework.py has the same
# exp_adjust fix), readback_resolve_max_bytes (patch_framework.py limits
# readback to 256 KB), the XMA, save and DLC fixes (MP3 has its own XMA port).
# Order: ci/pc_patches.py fw cd (on the pristine framework), ci/patch_framework.py fw,
# then this:  python3 ci/fw_perf_fixes.py fw cd
#
#!/usr/bin/env python3
# Framework fixes for Crackdown's frame rate and the menu's song, on top of
# ci/patch_framework.py (run after it, from the repo root):
#
#   python3 ci/fw_perf_fixes.py fw cd
#
# - The PC recomp's own patches to ReXGlue's waits (cd/patches/rexglue-sdk):
#   0015 the timer queue blocks instead of polling, 0017 the vblank thread
#   sleeps until the next vblank, 0019/0020 WAIT_REG_MEM yields, then waits on
#   guest progress (the vblank) for at most 1 ms instead of sleeping its hint,
#   0027 the command processor's idle wait isn't alertable (POSIX polls those
#   in 1 ms slices). The PS5 log showed the GPU thread 84% asleep while the
#   game thread spun waiting for it.
# - With those, the GPU thread was busy all the time (about 75 ms a frame, 6000
#   draws): 0024/0079 write runs of side-effect-free registers as a block, 0038
#   ends Vulkan submissions only when the GPU would run dry and doesn't wait for
#   occlusion queries, 0061/0062/0064/0066 log the stubs and warnings the game
#   hits thousands of times a minute once.
# - readback_resolve_max_bytes (the core of the PC recomp's 0056/0058, whose
#   deferred writeback half the fork doesn't have): resolves larger than it
#   aren't copied back into guest memory. The PC recomp's crackdown.toml sets
#   256 KB (CrackdownApp::OnPreSetup does here); copying every full-screen
#   resolve into the write-watched guest memory cost the GPU thread most.
# - gta4-ps5's sound mixer decodes an MP3 from memory: streaming it from the
#   app's drive inside the 5 ms audio deadline made the menu's song stutter.
import os
import re
import subprocess
import sys
from pathlib import Path

fw, cd = Path(sys.argv[1]), Path(sys.argv[2])
sdk = "glue/rexglue-sdk-main"

def edit(rel, old, new):
    path = fw / sdk / rel
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1 and os.environ.get("PATCH_KEEP_GOING"):  # [mp3] (list every mismatch)
        print(f"MISMATCH {rel}: {old[:200]!r}")
        return
    assert text.count(old) == 1, f"{rel}: {old[:60]!r} changed"
    path.write_text(text.replace(old, new), encoding="utf-8")

# Hot pages. The third PS5 log: about 1000 write watch faults a second (the game
# rewrites dynamic buffers every frame), and each one's re-protection - an
# mprotect on each of the three physical views - took 22% of the GPU thread.
# A page invalidated by the CPU in kHotPageFrames frames in a row stops being
# watched: at each new batch of GPU commands (the guest writes a batch's data
# before it moves the write pointer) its hash, taken when it was uploaded, is
# checked, and a changed page is invalidated like a fired watch. Quiet for
# kHotPageQuietFrames, it's watched again; written by the GPU, at once.
edit("include/rex/graphics/shared_memory.h",
     """  uint32_t CheckStalePages(uint32_t pages_per_frame);
""",
     """  uint32_t CheckStalePages(uint32_t pages_per_frame);

  // [crackdown] A new batch of GPU commands: the hot pages are checked before the next request.
  static void NoteCommandBatch() { command_batch_.fetch_add(1, std::memory_order_relaxed); }
""")
edit("include/rex/graphics/shared_memory.h",
     """  uint32_t stale_check_cursor_ = 0;
  uint64_t stale_pages_found_ = 0;
""",
     """  uint32_t stale_check_cursor_ = 0;
  uint64_t stale_pages_found_ = 0;

  // [crackdown] Pages the guest rewrites every frame ("hot"): not write watched - a watch fault
  // and the re-protection cost the PS5 far more than a hash - but checked against page_hashes_
  // at each new batch of GPU commands (CheckHotPages), by the GPU thread under the global lock.
  static constexpr uint32_t kMaxHotPages = 128;
  static constexpr uint8_t kHotPageFrames = 4;
  static constexpr uint32_t kHotPageQuietFrames = 300;
  inline static std::atomic<uint32_t> command_batch_{0};
  std::atomic<uint32_t> hot_frame_{8};
  uint32_t hot_check_batch_ = 0;
  std::vector<uint32_t> page_invalidation_frame_;  // the last frame the page changed
  std::vector<uint8_t> page_invalidation_streak_;  // frames in a row it changed in
  std::vector<uint64_t> hot_page_flags_;
  std::vector<uint32_t> hot_pages_;
  std::vector<std::pair<uint32_t, uint32_t>> watch_runs_;
  void CheckHotPages();
""")
edit("src/graphics/shared_memory.cpp",
     """  page_hashes_.assign(kBufferSize >> page_size_log2_, 0);
""",
     """  page_hashes_.assign(kBufferSize >> page_size_log2_, 0);
  page_invalidation_frame_.assign(kBufferSize >> page_size_log2_, 0);
  page_invalidation_streak_.assign(kBufferSize >> page_size_log2_, 0);
  hot_page_flags_.assign(num_system_page_flags_, 0);
  hot_pages_.clear();
  hot_pages_.reserve(kMaxHotPages);  // (never reallocated: RequestRanges reads its size unlocked)
""")
# MakeRangeValid: GPU-written pages stop being hot; hot pages aren't watched.
edit("src/graphics/shared_memory.cpp",
     """  {
    auto global_lock = global_critical_region_.Acquire();
    uint64_t* valid_flags = active_valid_flags_.load(std::memory_order_relaxed);

    for (uint32_t i = valid_block_first; i <= valid_block_last; ++i) {
      uint64_t valid_bits = UINT64_MAX;""",
     """  watch_runs_.clear();
  {
    auto global_lock = global_critical_region_.Acquire();
    uint64_t* valid_flags = active_valid_flags_.load(std::memory_order_relaxed);

    // [crackdown] the watched runs between hot pages; GPU-written pages are watched again
    uint32_t run_first = valid_page_first;
    if (!hot_pages_.empty()) {
      for (uint32_t page = valid_page_first; page <= valid_page_last; ++page) {
        const uint64_t bit = uint64_t(1) << (page & 63);
        if (!(hot_page_flags_[page >> 6] & bit)) {
          continue;
        }
        if (written_by_gpu) {
          hot_page_flags_[page >> 6] &= ~bit;
          for (uint32_t& hot : hot_pages_) {
            if (hot == page) {
              hot = hot_pages_.back();
              hot_pages_.pop_back();
              break;
            }
          }
          continue;
        }
        if (page > run_first) {
          watch_runs_.emplace_back(run_first, page - run_first);
        }
        run_first = page + 1;
      }
    }
    if (run_first <= valid_page_last) {
      watch_runs_.emplace_back(run_first, valid_page_last + 1 - run_first);
    }

    for (uint32_t i = valid_block_first; i <= valid_block_last; ++i) {
      uint64_t valid_bits = UINT64_MAX;""")
edit("src/graphics/shared_memory.cpp",
     """  if (memory_invalidation_callback_handle_) {
    memory().EnablePhysicalMemoryAccessCallbacks(
        valid_page_first << page_size_log2_,
        (valid_page_last - valid_page_first + 1) << page_size_log2_, true, false);
  }
}""",
     """  if (memory_invalidation_callback_handle_) {
    for (const std::pair<uint32_t, uint32_t>& run : watch_runs_) {
      memory().EnablePhysicalMemoryAccessCallbacks(run.first << page_size_log2_,
                                                   run.second << page_size_log2_, true, false);
    }
  }
}

void SharedMemory::CheckHotPages() {
  const uint32_t batch = command_batch_.load(std::memory_order_relaxed);
  if (batch == hot_check_batch_) {
    return;
  }
  hot_check_batch_ = batch;
  const uint32_t page_size = uint32_t(1) << page_size_log2_;
  const uint32_t frame = hot_frame_.load(std::memory_order_relaxed);
  auto global_lock = global_critical_region_.Acquire();
  const uint64_t* valid_flags = active_valid_flags_.load(std::memory_order_relaxed);
  for (size_t i = 0; i < hot_pages_.size();) {
    const uint32_t page = hot_pages_[i];
    const uint32_t address = page << page_size_log2_;
    const uint64_t bit = uint64_t(1) << (page & 63);
    if (frame - page_invalidation_frame_[page] > kHotPageQuietFrames) {
      // quiet: watched again (reloaded, and so protected, at its next use)
      hot_page_flags_[page >> 6] &= ~bit;
      hot_pages_[i] = hot_pages_.back();
      hot_pages_.pop_back();
      page_invalidation_streak_[page] = 0;
      page_hashes_[page] = 0;
      MemoryInvalidationCallback(address, page_size, true);
      continue;
    }
    ++i;
    // (a valid page is committed - freeing guest memory invalidates it in the same lock; one the
    // guest made unreadable keeps its GPU copy: reading it would fault)
    if (!valid_flags || !(valid_flags[page >> 6] & bit) || !page_hashes_[page]) {
      continue;
    }
    uint32_t protect = 0;
    auto* physical_heap = memory_.GetPhysicalHeap();
    if (!physical_heap || !physical_heap->QueryProtect(address, &protect) ||
        !(protect & memory::kMemoryProtectRead)) {
      continue;
    }
    if (XXH3_64bits(memory_.TranslatePhysical(address), page_size) == page_hashes_[page]) {
      continue;
    }
    page_invalidation_frame_[page] = frame;
    page_hashes_[page] = 0;
    MemoryInvalidationCallback(address, page_size, true);
  }
}""")
edit("src/graphics/shared_memory.cpp",
     """  if (ranges == nullptr || !count) {
    return true;
  }
""",
     """  if (ranges == nullptr || !count) {
    return true;
  }
  if (!hot_pages_.empty()) {
    CheckHotPages();
  }
""")
edit("src/graphics/shared_memory.cpp",
     """uint32_t SharedMemory::CheckStalePages(uint32_t pages_per_frame) {
""",
     """uint32_t SharedMemory::CheckStalePages(uint32_t pages_per_frame) {
  hot_frame_.fetch_add(1, std::memory_order_relaxed);  // (once a frame, from IssueSwap)
""")
# A CPU write's invalidation: pages changing frame after frame become hot.
edit("src/graphics/shared_memory.cpp",
     """  auto global_lock = global_critical_region_.Acquire();

  if (!exact_range) {""",
     """  auto global_lock = global_critical_region_.Acquire();

  if (page_last - page_first < 16) {
    const uint32_t frame = hot_frame_.load(std::memory_order_relaxed);
    for (uint32_t page = page_first; page <= page_last; ++page) {
      const uint64_t bit = uint64_t(1) << (page & 63);
      const uint32_t last = page_invalidation_frame_[page];
      if (last == frame || (hot_page_flags_[page >> 6] & bit)) {
        continue;
      }
      uint8_t& streak = page_invalidation_streak_[page];
      streak = frame - last == 1 ? uint8_t(std::min(int(streak) + 1, 255)) : uint8_t(1);
      page_invalidation_frame_[page] = frame;
      if (streak >= kHotPageFrames && hot_pages_.size() < kMaxHotPages &&
          !(system_page_flags_valid_and_gpu_written_[page >> 6] & bit)) {
        hot_page_flags_[page >> 6] |= bit;
        hot_pages_.push_back(page);
      }
    }
  }

  if (!exact_range) {""")
edit("src/graphics/command_processor.cpp",
     """  ArmNativeRendererOracleCapture();
""",
     """  ArmNativeRendererOracleCapture();
  // [crackdown] the guest wrote this batch's data before moving the write pointer
  SharedMemory::NoteCommandBatch();
""")
text = (fw / sdk / "src/graphics/command_processor.cpp").read_text(encoding="utf-8")
if "#include <rex/graphics/shared_memory.h>" not in text:
    edit("src/graphics/command_processor.cpp", "#include <rex/graphics/flags.h>\n",
         "#include <rex/graphics/flags.h>\n#include <rex/graphics/shared_memory.h>\n")
print("shared memory: hot pages hashed instead of watched")

# Texture bindings. The PS5 log's GPU thread was busy all the time, the game's main thread
# waiting on it; a fifth of it went to writing and binding a new texture/sampler descriptor set
# for every draw (the fork's "TODO: Reuse texture and sampler bindings if not changed"). A stage
# whose views, samplers and set layout are the previous draw's, in the same frame (the sets are
# the frame's), keeps its set. And a pipeline layout change now drops the bindings it disturbs
# (descriptor_sets_kept was computed and never applied; rewriting every draw hid it).
edit("include/rex/graphics/vulkan/command_processor.h",
     """  std::vector<VkDescriptorImageInfo> descriptor_write_image_info_;
""",
     """  std::vector<VkDescriptorImageInfo> descriptor_write_image_info_;
  // [crackdown] the texture bindings last written for each stage (0 vertex, 1 pixel)
  struct TextureBindingMemo {
    uint64_t frame = 0;
    VkDescriptorSet set = VK_NULL_HANDLE;
    VkDescriptorSetLayout layout = VK_NULL_HANDLE;
    uint32_t texture_count = 0;
    uint32_t sampler_count = 0;
    std::vector<VkDescriptorImageInfo> infos;
  };
  TextureBindingMemo texture_binding_memo_[2];
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """        descriptor_sets_kept = std::min(
            descriptor_sets_kept, uint32_t(SpirvShaderTranslator::kDescriptorSetTexturesPixel));
      }
""",
     """        descriptor_sets_kept = std::min(
            descriptor_sets_kept, uint32_t(SpirvShaderTranslator::kDescriptorSetTexturesPixel));
      }
      current_graphics_descriptor_sets_bound_up_to_date_ &= (UINT32_C(1) << descriptor_sets_kept) - 1;
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """  // TODO(Triang3l): Reuse texture and sampler bindings if not changed.
  current_graphics_descriptor_set_values_up_to_date_ &=
      ~((UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesVertex) |
        (UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesPixel));

  // Make sure new descriptor sets are bound to the command buffer.

  current_graphics_descriptor_sets_bound_up_to_date_ &=
      current_graphics_descriptor_set_values_up_to_date_;

  // Fill the texture and sampler write image infos.

  bool write_vertex_textures =
      (texture_count_vertex || sampler_count_vertex) &&
      !(current_graphics_descriptor_set_values_up_to_date_ &
        (UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesVertex));
  bool write_pixel_textures =
      (texture_count_pixel || sampler_count_pixel) &&
      !(current_graphics_descriptor_set_values_up_to_date_ &
        (UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesPixel));
""",
     """  // [crackdown] texture and sampler bindings are kept below when unchanged
  current_graphics_descriptor_set_values_up_to_date_ &=
      ~((UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesVertex) |
        (UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesPixel));

  // Fill the texture and sampler write image infos.

  bool write_vertex_textures = texture_count_vertex || sampler_count_vertex;
  bool write_pixel_textures = texture_count_pixel || sampler_count_pixel;
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """  // Write the new descriptor sets.

""",
     """  // [crackdown] A stage whose bindings are the previous draw's keeps its set (this frame's).
  auto texture_bindings_unchanged = [&](uint32_t stage, VkDescriptorSetLayout layout,
                                        uint32_t texture_count, uint32_t sampler_count,
                                        size_t texture_offset, size_t sampler_offset) {
    const TextureBindingMemo& memo = texture_binding_memo_[stage];
    const uint32_t set_index = stage ? uint32_t(SpirvShaderTranslator::kDescriptorSetTexturesPixel)
                                     : uint32_t(SpirvShaderTranslator::kDescriptorSetTexturesVertex);
    if (memo.frame != frame_current_ || memo.set == VK_NULL_HANDLE ||
        current_graphics_descriptor_sets_[set_index] != memo.set || memo.layout != layout ||
        memo.texture_count != texture_count || memo.sampler_count != sampler_count) {
      return false;
    }
    for (uint32_t i = 0; i < texture_count; ++i) {
      const VkDescriptorImageInfo& a = descriptor_write_image_info_[texture_offset + i];
      const VkDescriptorImageInfo& b = memo.infos[i];
      if (a.imageView != b.imageView || a.imageLayout != b.imageLayout) {
        return false;
      }
    }
    for (uint32_t i = 0; i < sampler_count; ++i) {
      if (descriptor_write_image_info_[sampler_offset + i].sampler !=
          memo.infos[texture_count + i].sampler) {
        return false;
      }
    }
    return true;
  };
  auto remember_texture_bindings = [&](uint32_t stage, VkDescriptorSet set,
                                       VkDescriptorSetLayout layout, uint32_t texture_count,
                                       uint32_t sampler_count, size_t texture_offset,
                                       size_t sampler_offset) {
    TextureBindingMemo& memo = texture_binding_memo_[stage];
    memo.frame = frame_current_;
    memo.set = set;
    memo.layout = layout;
    memo.texture_count = texture_count;
    memo.sampler_count = sampler_count;
    memo.infos.assign(descriptor_write_image_info_.begin() + texture_offset,
                      descriptor_write_image_info_.begin() + texture_offset + texture_count);
    memo.infos.insert(memo.infos.end(), descriptor_write_image_info_.begin() + sampler_offset,
                      descriptor_write_image_info_.begin() + sampler_offset + sampler_count);
  };
  const VkDescriptorSetLayout textures_layout_vertex =
      current_guest_graphics_pipeline_layout_->descriptor_set_layout_textures_vertex_ref();
  const VkDescriptorSetLayout textures_layout_pixel =
      current_guest_graphics_pipeline_layout_->descriptor_set_layout_textures_pixel_ref();
  if (write_vertex_textures &&
      texture_bindings_unchanged(0, textures_layout_vertex, texture_count_vertex,
                                 sampler_count_vertex, vertex_texture_image_info_offset,
                                 vertex_sampler_image_info_offset)) {
    write_vertex_textures = false;
    current_graphics_descriptor_set_values_up_to_date_ |=
        UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesVertex;
  }
  if (write_pixel_textures &&
      texture_bindings_unchanged(1, textures_layout_pixel, texture_count_pixel,
                                 sampler_count_pixel, pixel_texture_image_info_offset,
                                 pixel_sampler_image_info_offset)) {
    write_pixel_textures = false;
    current_graphics_descriptor_set_values_up_to_date_ |=
        UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetTexturesPixel;
  }

  // Make sure new descriptor sets are bound to the command buffer.
  current_graphics_descriptor_sets_bound_up_to_date_ &=
      current_graphics_descriptor_set_values_up_to_date_;

  // Write the new descriptor sets.

""")
edit("src/graphics/vulkan/command_processor.cpp",
     """    current_graphics_descriptor_sets_[SpirvShaderTranslator::kDescriptorSetTexturesVertex] =
        write_textures[0].dstSet;
""",
     """    current_graphics_descriptor_sets_[SpirvShaderTranslator::kDescriptorSetTexturesVertex] =
        write_textures[0].dstSet;
    remember_texture_bindings(0, write_textures[0].dstSet, textures_layout_vertex,
                              texture_count_vertex, sampler_count_vertex,
                              vertex_texture_image_info_offset, vertex_sampler_image_info_offset);
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """    current_graphics_descriptor_sets_[SpirvShaderTranslator::kDescriptorSetTexturesPixel] =
        write_textures[0].dstSet;
""",
     """    current_graphics_descriptor_sets_[SpirvShaderTranslator::kDescriptorSetTexturesPixel] =
        write_textures[0].dstSet;
    remember_texture_bindings(1, write_textures[0].dstSet, textures_layout_pixel,
                              texture_count_pixel, sampler_count_pixel,
                              pixel_texture_image_info_offset, pixel_sampler_image_info_offset);
""")
print("vulkan: unchanged texture bindings keep their descriptor set")

# A kept texture binding set holds the descriptors of the views it was written with: when the
# texture cache destroys textures, the kept sets are dropped, so a destroyed view whose handle is
# reused is never mistaken for an unchanged one. (Not on the other binding resets: an outdated
# texture is reloaded into the same image and view.)
edit("include/rex/graphics/pipeline/texture/cache.h",
     """class TextureCache {
""",
     """// [crackdown] Counts texture destructions (kept descriptor sets are dropped).
inline std::atomic<uint32_t> texture_binding_reset_generation{0};

class TextureCache {
""")
edit("src/graphics/pipeline/texture/cache.cpp",
     """    // Remove the texture from the map and destroy it via its unique_ptr.
""",
     """    // Remove the texture from the map and destroy it via its unique_ptr.
    texture_binding_reset_generation.fetch_add(1, std::memory_order_relaxed);
""")
edit("src/graphics/pipeline/texture/cache.cpp",
     """void TextureCache::DestroyAllTextures(bool from_destructor) {
""",
     """void TextureCache::DestroyAllTextures(bool from_destructor) {
  texture_binding_reset_generation.fetch_add(1, std::memory_order_relaxed);
""")
edit("include/rex/graphics/vulkan/command_processor.h",
     """    uint64_t frame = 0;
    VkDescriptorSet set = VK_NULL_HANDLE;
""",
     """    uint64_t frame = 0;
    uint32_t reset_generation = 0;
    VkDescriptorSet set = VK_NULL_HANDLE;
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """    if (memo.frame != frame_current_ || memo.set == VK_NULL_HANDLE ||
""",
     """    if (memo.frame != frame_current_ || memo.set == VK_NULL_HANDLE ||
        memo.reset_generation !=
            texture_binding_reset_generation.load(std::memory_order_relaxed) ||
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """    memo.frame = frame_current_;
    memo.set = set;
""",
     """    memo.frame = frame_current_;
    memo.reset_generation = texture_binding_reset_generation.load(std::memory_order_relaxed);
    memo.set = set;
""")
print("vulkan: kept texture bindings dropped when the texture cache resets its bindings")

# The texture cache read the host clock at every completed-submission check, which the PS5 log
# put at 3% of the GPU thread; it is only needed to age a texture over the soft memory limit.
edit("src/graphics/pipeline/texture/cache.cpp",
     """  // If memory usage is too high, destroy unused textures.
  uint64_t current_time = rex::chrono::Clock::QueryHostUptimeMillis();
""",
     """  // If memory usage is too high, destroy unused textures.
  uint64_t current_time = 0;  // [crackdown] read once a texture is aged
""")
edit("src/graphics/pipeline/texture/cache.cpp",
     """    if (!limit_hard_exceeded && (texture->last_usage_time() + limit_soft_lifetime) > current_time) {
""",
     """    if (!limit_hard_exceeded && !current_time) {
      current_time = rex::chrono::Clock::QueryHostUptimeMillis();
    }
    if (!limit_hard_exceeded && (texture->last_usage_time() + limit_soft_lifetime) > current_time) {
""")
print("texture cache: the host clock read only to age a texture")

# CPU layout (crackdown-recomp/src/core_layout_ps5.cpp): the GPU command thread, which sets the
# frame rate, takes CPU 0 when it starts, and the game's main thread keeps the others off its core.
edit("include/rex/ps5_diag.h",
     """inline void Add(Counter c) { counters[c].fetch_add(1, std::memory_order_relaxed); }
""",
     """inline void Add(Counter c) { counters[c].fetch_add(1, std::memory_order_relaxed); }
// [crackdown] The GPU command processor's thread (its pthread_t; 0 until it starts).
inline std::atomic<uintptr_t> gpu_commands_thread{0};
""")
edit("src/graphics/command_processor.cpp",
     """#include <rex/ps5_diag.h>
""",
     """#include <rex/ps5_diag.h>
#if defined(__PROSPERO__)
#include <pthread.h>
extern "C" int32_t scePthreadSetaffinity(pthread_t thread, uint64_t mask);
#endif
""")
edit("src/graphics/command_processor.cpp",
     """void CommandProcessor::WorkerThreadMain() {
  if (!SetupContext()) {
    rex::FatalError("Unable to setup command processor internal state");
    return;
  }
""",
     """void CommandProcessor::WorkerThreadMain() {
  if (!SetupContext()) {
    rex::FatalError("Unable to setup command processor internal state");
    return;
  }
#if defined(__PROSPERO__)
  // [crackdown] CPU 0, a core of its own (CPU 1, the core's other half, stays empty)
  scePthreadSetaffinity(pthread_self(), uint64_t(1) << 0);
  rex::ps5diag::gpu_commands_thread.store(reinterpret_cast<uintptr_t>(pthread_self()),
                                          std::memory_order_release);
#endif
""")
print("cpu layout: the GPU command thread on a core of its own")

sound = fw / sdk / "gta4-recomp/src/ps5/game/sound.cpp"
text = sound.read_text(encoding="utf-8")
old = """  bool Open(const std::filesystem::path& f) {
    if (!drmp3_init_file(&mp3_, f.string().c_str(), nullptr)) return false;"""
new = """  bool Open(const std::filesystem::path& f) {
    // (from memory: reading the drive in the audio thread misses its deadline)
    std::FILE* file = std::fopen(f.string().c_str(), "rb");
    if (!file) return false;
    std::fseek(file, 0, SEEK_END);
    const long size = std::ftell(file);
    std::fseek(file, 0, SEEK_SET);
    data_.resize(size > 0 ? size_t(size) : 0);
    const bool read = !data_.empty() && std::fread(data_.data(), 1, data_.size(), file) == data_.size();
    std::fclose(file);
    if (!read || !drmp3_init_memory(&mp3_, data_.data(), data_.size(), nullptr)) return false;"""
assert text.count(old) == 1, "sound.cpp: Mp3::Open changed"
text = text.replace(old, new)
old = """ private:
  drmp3 mp3_{};"""
assert text.count(old) == 1, "sound.cpp: Mp3's members changed"
text = text.replace(old, """ private:
  std::vector<uint8_t> data_;
  drmp3 mp3_{};""")
if "#include <vector>" not in text:
    text = text.replace("#include <string>\n", "#include <string>\n#include <vector>\n", 1)
sound.write_text(text, encoding="utf-8")
print("sound.cpp: MP3s decoded from memory")

# Achievements: the fork's XGIUserWriteAchievements took only GTA IV's ids (1-65), so Crackdown's
# first unlocks (ids 67 and on) were refused. Any id in the title's own list is taken now.
edit("src/kernel/xam/apps/xgi_app.cpp",
     """      if (achievement_count > kLastAchievementId) {""",
     """      if (achievement_count > 256) {  // [crackdown] (a sanity limit: the list is the title's)""")
edit("src/kernel/xam/apps/xgi_app.cpp",
     """        if (id < kFirstAchievementId || id > kLastAchievementId) {""",
     """        if (id < kFirstAchievementId || !kernel_state_->achievements().FindAchievement(id)) {  // [crackdown] the title's list""")
print("achievements: any id in the title's list is unlocked")

# Constant buffers as dynamic uniform buffers. The PS5 gameplay profile (crackdown_019.log, about
# 6400 draws a frame) had a fifth of the GPU thread in descriptors: every draw that changed a
# constant wrote a new set of five uniform buffer descriptors. The constants live in the uniform
# pool's 2 MB pages, kept for the session, so a set per combination of pages is written once and
# each draw binds it with the constants' offsets.
edit("include/rex/ui/graphics_upload_buffer_pool.h",
     """  virtual ~GraphicsUploadBufferPool();
""",
     """  virtual ~GraphicsUploadBufferPool();

  size_t page_size() const { return page_size_; }  // [crackdown]
""")
edit("include/rex/graphics/vulkan/command_processor.h",
     """  TextureBindingMemo texture_binding_memo_[2];
""",
     """  TextureBindingMemo texture_binding_memo_[2];
  // [crackdown] the constant buffers' sets (dynamic uniform buffers), one per combination of the
  // uniform pool's pages, and the current draw's offsets into them
  VkDescriptorPool constants_dynamic_pool_ = VK_NULL_HANDLE;
  std::vector<VkDescriptorPool> constants_dynamic_pools_retired_;
  std::map<std::array<VkBuffer, SpirvShaderTranslator::kConstantBufferCount>, VkDescriptorSet>
      constants_dynamic_sets_;
  uint32_t constants_dynamic_offsets_[SpirvShaderTranslator::kConstantBufferCount] = {};
""")
edit("include/rex/graphics/vulkan/command_processor.h",
     "#include <deque>\n",
     "#include <deque>\n#include <map>\n")
edit("src/graphics/vulkan/command_processor.cpp",
     """    constants_binding.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
""",
     """    constants_binding.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;  // [crackdown]
""")
# (a float constants binding reads up to 256 vectors from its offset: one near the end of a page
# goes to the next page)
path = fw / sdk / "src/graphics/vulkan/command_processor.cpp"
text = path.read_text(encoding="utf-8")
old = """      uint8_t* mapping = uniform_buffer_pool_->Request(frame_current_, float_constants_size,
                                                       uniform_buffer_alignment, buffer_info.buffer,
                                                       buffer_info.offset);
      if (!mapping) {
        return false;
      }
"""
assert text.count(old) == 2, "command_processor.cpp: the float constants' requests changed"
text = text.replace(old, """      uint8_t* mapping = uniform_buffer_pool_->Request(frame_current_, float_constants_size,
                                                       uniform_buffer_alignment, buffer_info.buffer,
                                                       buffer_info.offset);
      if (mapping && buffer_info.offset + kConstantsFloatRange > uniform_buffer_pool_->page_size()) {
        mapping = uniform_buffer_pool_->Request(frame_current_, kConstantsFloatRange,
                                                uniform_buffer_alignment, buffer_info.buffer,
                                                buffer_info.offset);
      }
      if (!mapping) {
        return false;
      }
""")
path.write_text(text, encoding="utf-8")
edit("src/graphics/vulkan/command_processor.cpp",
     """bool VulkanCommandProcessor::UpdateBindings(const VulkanShader* vertex_shader,
                                            const VulkanShader* pixel_shader) {
""",
     """// [crackdown] the bytes each constant buffer binding reads from its dynamic offset
constexpr VkDeviceSize kConstantsFloatRange = sizeof(float) * 4 * 256;

bool VulkanCommandProcessor::UpdateBindings(const VulkanShader* vertex_shader,
                                            const VulkanShader* pixel_shader) {
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """  // Constant buffers.
  if (!(current_graphics_descriptor_set_values_up_to_date_ &
        (UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetConstants))) {
    VkDescriptorSet constants_descriptor_set;
    if (!constants_transient_descriptors_free_.empty()) {
      constants_descriptor_set = constants_transient_descriptors_free_.back();
      constants_transient_descriptors_free_.pop_back();
    } else {
      VkDescriptorPoolSize constants_descriptor_count;
      constants_descriptor_count.type = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
      constants_descriptor_count.descriptorCount = SpirvShaderTranslator::kConstantBufferCount;
      constants_descriptor_set = transient_descriptor_allocator_uniform_buffer_.Allocate(
          descriptor_set_layout_constants_, &constants_descriptor_count, 1);
      if (constants_descriptor_set == VK_NULL_HANDLE) {
        return false;
      }
    }
    constants_transient_descriptors_used_.emplace_back(frame_current_, constants_descriptor_set);
    // Consecutive bindings updated via a single VkWriteDescriptorSet must have
    // identical stage flags, but for the constants they vary.
    for (uint32_t i = 0; i < SpirvShaderTranslator::kConstantBufferCount; ++i) {
      VkWriteDescriptorSet& write_constants = write_descriptor_sets[write_descriptor_set_count++];
      write_constants.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
      write_constants.pNext = nullptr;
      write_constants.dstSet = constants_descriptor_set;
      write_constants.dstBinding = i;
      write_constants.dstArrayElement = 0;
      write_constants.descriptorCount = 1;
      write_constants.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
      write_constants.pImageInfo = nullptr;
      write_constants.pBufferInfo = &current_constant_buffer_infos_[i];
      write_constants.pTexelBufferView = nullptr;
    }
    write_descriptor_set_bits |= UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetConstants;
""",
     """  // Constant buffers: [crackdown] the set for their pages (written once), bound with their
  // offsets.
  if (!(current_graphics_descriptor_set_values_up_to_date_ &
        (UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetConstants))) {
    std::array<VkBuffer, SpirvShaderTranslator::kConstantBufferCount> constants_buffers;
    for (uint32_t i = 0; i < SpirvShaderTranslator::kConstantBufferCount; ++i) {
      constants_buffers[i] = current_constant_buffer_infos_[i].buffer;
      constants_dynamic_offsets_[i] = uint32_t(current_constant_buffer_infos_[i].offset);
    }
    VkDescriptorSet constants_descriptor_set;
    auto constants_set_it = constants_dynamic_sets_.find(constants_buffers);
    if (constants_set_it != constants_dynamic_sets_.end()) {
      constants_descriptor_set = constants_set_it->second;
    } else {
      constexpr uint32_t kConstantsDynamicSets = 4096;
      if (constants_dynamic_sets_.size() >= kConstantsDynamicSets) {
        // (more combinations than seen in play: a new pool - the old one's sets may still be in
        // flight, so it's kept until the caches are cleared)
        constants_dynamic_pools_retired_.push_back(constants_dynamic_pool_);
        constants_dynamic_pool_ = VK_NULL_HANDLE;
        constants_dynamic_sets_.clear();
      }
      if (constants_dynamic_pool_ == VK_NULL_HANDLE) {
        VkDescriptorPoolSize pool_size;
        pool_size.type = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
        pool_size.descriptorCount = kConstantsDynamicSets * SpirvShaderTranslator::kConstantBufferCount;
        VkDescriptorPoolCreateInfo pool_info = {};
        pool_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
        pool_info.maxSets = kConstantsDynamicSets;
        pool_info.poolSizeCount = 1;
        pool_info.pPoolSizes = &pool_size;
        if (dfn.vkCreateDescriptorPool(device, &pool_info, nullptr, &constants_dynamic_pool_) !=
            VK_SUCCESS) {
          constants_dynamic_pool_ = VK_NULL_HANDLE;
          return false;
        }
      }
      VkDescriptorSetAllocateInfo allocate_info = {};
      allocate_info.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
      allocate_info.descriptorPool = constants_dynamic_pool_;
      allocate_info.descriptorSetCount = 1;
      allocate_info.pSetLayouts = &descriptor_set_layout_constants_;
      if (dfn.vkAllocateDescriptorSets(device, &allocate_info, &constants_descriptor_set) !=
          VK_SUCCESS) {
        return false;
      }
      const VkDeviceSize ranges[SpirvShaderTranslator::kConstantBufferCount] = {
          sizeof(SpirvShaderTranslator::SystemConstants), kConstantsFloatRange,
          kConstantsFloatRange, sizeof(uint32_t) * (8 + 32), sizeof(uint32_t) * 6 * 32};
      VkDescriptorBufferInfo infos[SpirvShaderTranslator::kConstantBufferCount];
      VkWriteDescriptorSet writes[SpirvShaderTranslator::kConstantBufferCount];
      for (uint32_t i = 0; i < SpirvShaderTranslator::kConstantBufferCount; ++i) {
        infos[i].buffer = constants_buffers[i];
        infos[i].offset = 0;
        infos[i].range = ranges[i];
        VkWriteDescriptorSet& write_constants = writes[i];
        write_constants.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        write_constants.pNext = nullptr;
        write_constants.dstSet = constants_descriptor_set;
        write_constants.dstBinding = i;
        write_constants.dstArrayElement = 0;
        write_constants.descriptorCount = 1;
        write_constants.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
        write_constants.pImageInfo = nullptr;
        write_constants.pBufferInfo = &infos[i];
        write_constants.pTexelBufferView = nullptr;
      }
      dfn.vkUpdateDescriptorSets(device, SpirvShaderTranslator::kConstantBufferCount, writes, 0,
                                 nullptr);
      constants_dynamic_sets_.emplace(constants_buffers, constants_descriptor_set);
    }
    write_descriptor_set_bits |= UINT32_C(1) << SpirvShaderTranslator::kDescriptorSetConstants;
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """    deferred_command_buffer_.CmdVkBindDescriptorSets(
        VK_PIPELINE_BIND_POINT_GRAPHICS,
        current_guest_graphics_pipeline_layout_->GetPipelineLayout(), descriptor_set_index,
        descriptor_set_mask_tzcnt - descriptor_set_index,
        current_graphics_descriptor_sets_ + descriptor_set_index, 0, nullptr);
""",
     """    // [crackdown] (the constants' set takes their offsets)
    const bool binds_constants =
        descriptor_set_index <= uint32_t(SpirvShaderTranslator::kDescriptorSetConstants) &&
        descriptor_set_mask_tzcnt > uint32_t(SpirvShaderTranslator::kDescriptorSetConstants);
    deferred_command_buffer_.CmdVkBindDescriptorSets(
        VK_PIPELINE_BIND_POINT_GRAPHICS,
        current_guest_graphics_pipeline_layout_->GetPipelineLayout(), descriptor_set_index,
        descriptor_set_mask_tzcnt - descriptor_set_index,
        current_graphics_descriptor_sets_ + descriptor_set_index,
        binds_constants ? uint32_t(SpirvShaderTranslator::kConstantBufferCount) : 0,
        binds_constants ? constants_dynamic_offsets_ : nullptr);
""")
edit("src/graphics/vulkan/command_processor.cpp",
     """void VulkanCommandProcessor::ClearTransientDescriptorPools() {
""",
     """void VulkanCommandProcessor::ClearTransientDescriptorPools() {
  // [crackdown] (the constants' sets refer to the uniform pool's pages, cleared with it)
  constants_dynamic_sets_.clear();
  if (constants_dynamic_pool_ != VK_NULL_HANDLE) {
    constants_dynamic_pools_retired_.push_back(constants_dynamic_pool_);
    constants_dynamic_pool_ = VK_NULL_HANDLE;
  }
  const ui::vulkan::VulkanDevice* const pool_device = GetVulkanDevice();
  for (VkDescriptorPool pool : constants_dynamic_pools_retired_) {
    pool_device->functions().vkDestroyDescriptorPool(pool_device->device(), pool, nullptr);
  }
  constants_dynamic_pools_retired_.clear();
""")
print("vulkan: constant buffers bound as dynamic uniform buffers")

# Hot pages checked where they're used. CheckHotPages hashed every hot page (up to 128, 512 KB) at
# each new batch of GPU commands - many a frame - which the PS5 gameplay profile put at 7% of the
# GPU thread. Now a batch hashes only the hot pages its draws request (once each), and all of
# them are checked once a frame (with the stale page scan): a texture on one of them, which isn't
# requested again until it's invalidated, is seen within the frame.
edit("include/rex/graphics/shared_memory.h",
     """  void CheckHotPages();
""",
     """  void CheckHotPages();
  // [crackdown] the hot pages in the ranges, each once a batch
  void CheckHotPagesIn(const std::vector<std::pair<uint32_t, uint32_t>>& ranges);
  std::vector<uint32_t> hot_page_checked_batch_;
""")
edit("src/graphics/shared_memory.cpp",
     """  if (!hot_pages_.empty()) {
    CheckHotPages();
  }

  // Some texture or buffer is empty, for example - safe to draw in this case.
""",
     """  // Some texture or buffer is empty, for example - safe to draw in this case.
""")
edit("src/graphics/shared_memory.cpp",
     """  merged_ranges.resize(merged_write + 1);

  for (const std::pair<uint32_t, uint32_t>& range : merged_ranges) {
    if (!EnsureHostGpuMemoryAllocated(range.first, range.second)) {
""",
     """  merged_ranges.resize(merged_write + 1);

  if (!hot_pages_.empty()) {
    CheckHotPagesIn(merged_ranges);  // [crackdown]
  }

  for (const std::pair<uint32_t, uint32_t>& range : merged_ranges) {
    if (!EnsureHostGpuMemoryAllocated(range.first, range.second)) {
""")
edit("src/graphics/shared_memory.cpp",
     """void SharedMemory::UnlinkWatchRange(WatchRange* range) {
""",
     """void SharedMemory::CheckHotPagesIn(const std::vector<std::pair<uint32_t, uint32_t>>& ranges) {
  const uint32_t batch = command_batch_.load(std::memory_order_relaxed);
  const uint32_t page_size = uint32_t(1) << page_size_log2_;
  const uint32_t frame = hot_frame_.load(std::memory_order_relaxed);
  if (hot_page_checked_batch_.size() != page_hashes_.size()) {
    hot_page_checked_batch_.assign(page_hashes_.size(), batch - 1);
  }
  auto global_lock = global_critical_region_.Acquire();
  const uint64_t* valid_flags = active_valid_flags_.load(std::memory_order_relaxed);
  for (const std::pair<uint32_t, uint32_t>& range : ranges) {
    if (!range.second) {
      continue;
    }
    const uint32_t page_first = range.first >> page_size_log2_;
    const uint32_t page_last = std::min(uint32_t((uint64_t(range.first) + range.second - 1) >> page_size_log2_),
                                        uint32_t(page_hashes_.size() - 1));
    for (uint32_t block = page_first >> 6; block <= (page_last >> 6); ++block) {
      uint64_t bits = hot_page_flags_[block];
      if (block == (page_first >> 6)) {
        bits &= ~((uint64_t(1) << (page_first & 63)) - 1);
      }
      if (block == (page_last >> 6) && (page_last & 63) != 63) {
        bits &= (uint64_t(1) << ((page_last & 63) + 1)) - 1;
      }
      uint32_t index;
      while (rex::bit_scan_forward(bits, &index)) {
        bits &= bits - 1;
        const uint32_t page = (block << 6) + index;
        if (hot_page_checked_batch_[page] == batch) {
          continue;
        }
        hot_page_checked_batch_[page] = batch;
        const uint64_t bit = uint64_t(1) << index;
        if (!valid_flags || !(valid_flags[block] & bit) || !page_hashes_[page]) {
          continue;
        }
        const uint32_t address = page << page_size_log2_;
        uint32_t protect = 0;
        auto* physical_heap = memory_.GetPhysicalHeap();
        if (!physical_heap || !physical_heap->QueryProtect(address, &protect) ||
            !(protect & memory::kMemoryProtectRead)) {
          continue;
        }
        if (XXH3_64bits(memory_.TranslatePhysical(address), page_size) == page_hashes_[page]) {
          continue;
        }
        page_invalidation_frame_[page] = frame;
        page_hashes_[page] = 0;
        MemoryInvalidationCallback(address, page_size, true);
      }
    }
  }
}

void SharedMemory::UnlinkWatchRange(WatchRange* range) {
""")
edit("src/graphics/shared_memory.cpp",
     """  hot_frame_.fetch_add(1, std::memory_order_relaxed);  // (once a frame, from IssueSwap)
""",
     """  hot_frame_.fetch_add(1, std::memory_order_relaxed);  // (once a frame, from IssueSwap)
  if (!hot_pages_.empty()) {
    // [crackdown] every hot page, once a frame (a batch checks those it uses)
    hot_check_batch_ = command_batch_.load(std::memory_order_relaxed) - 1;
    CheckHotPages();
  }
""")
print("shared memory: hot pages checked where a batch uses them, all of them once a frame")

# The GPU thread's second lane (include/rex/graphics/gpu_lane.h): crackdown_019.log's gameplay had
# the GPU command thread busy all the time and the game's main thread waiting on it 63% of the time.
# Of the command thread's time, 7.5% went to the write watches' page protection (three mprotect
# calls a range, one per guest view of physical memory), 3.4% to copying guest memory into the
# upload buffer and about 5% to writing texture descriptor sets: none of it is needed before the
# submission is recorded and sent, so a thread of its own (CPU 4) does it while the command thread
# goes on with the next draws. It is waited for before a submission and before the command
# processor writes anything the game sees (a fence, the read pointer, a scratch register, an
# interrupt) - past that the game may reuse the memory a pending copy still has to read.
(fw / sdk / "include/rex/graphics/gpu_lane.h").write_text(r'''// [crackdown] The GPU command thread's second lane (ci/fw_perf_fixes.py in
// crackdown-ps5): a thread of its own that does the part of a draw's work only
// needed before its submission is sent - guest memory copied into the upload
// buffer, the pages' write watches armed before that, descriptor sets written -
// while the command thread goes on with the next draws.
//
// Drain() waits for every job pushed so far: before a submission's commands are
// recorded, and before the command processor writes anything the game can see
// (a fence, the ring buffer's read pointer, a scratch register, an interrupt),
// since past that the game may change the memory a pending copy has to read -
// as on the Xbox 360, where the GPU reads it any time before that point.
//
// crackdown_gpu_lane (on; the menu's GPU HELPER) is read at the first job.
#pragma once

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

#include <immintrin.h>

#include <rex/graphics/flags.h>
#include <rex/logging.h>
#include <rex/memory/utils.h>
#include <rex/ps5_diag.h>
#include <rex/thread/mutex.h>

#if defined(__PROSPERO__)
#include <pthread.h>
extern "C" int32_t scePthreadSetaffinity(pthread_t thread, uint64_t mask);
#endif

namespace rex::memory {
// (xmemory.cpp) On the lane's thread a watch only sets its pages' flags, under
// the global lock, and leaves their protection in deferred_protects: the lane
// changes it, merged, once it has let go of the lock.
struct DeferredProtect {
  uint8_t* address;
  size_t length;
  PageAccess access;
};
extern thread_local bool protect_after_unlock;
extern thread_local std::vector<DeferredProtect> deferred_protects;
}  // namespace rex::memory

namespace rex::graphics {

class GpuLane {
 public:
  using Call = void (*)(void* context, uint64_t argument);

  static GpuLane& Get() {
    static GpuLane lane;
    return lane;
  }

  // The rest is for the GPU command thread only.

  // Whether jobs go to the lane (its thread starts at the first call).
  bool Active() {
    if (state_ == kUnknown) {
      Start();
    }
    return state_ == kRunning;
  }

  void Copy(void* destination, const void* source, size_t size) {
    copied_ += size;
    Push({nullptr, nullptr, 0, destination, source, size, false});
  }
  void Run(Call call, void* context, uint64_t argument) {
    Push({call, context, argument, nullptr, nullptr, 0, false});
  }
  // A call that arms write watches: the lane runs the ones in a row under one
  // hold of the global lock.
  void Watch(Call call, void* context, uint64_t argument) {
    Push({call, context, argument, nullptr, nullptr, 0, true});
  }

  // Everything pushed so far done.
  bool Idle() const {
    return state_ != kRunning || done_.load(std::memory_order_acquire) == pushed_;
  }

  void Drain() {
    if (Idle()) {
      return;
    }
    const auto start = std::chrono::steady_clock::now();
    {
      std::unique_lock<std::mutex> lock(mutex_);
      drain_waiting_ = true;
      if (sleeping_) {
        work_cv_.notify_one();
      }
      done_cv_.wait(lock, [&] { return done_.load(std::memory_order_acquire) == pushed_; });
      drain_waiting_ = false;
    }
    ++drains_;
    drain_wait_us_ += uint64_t(
        std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - start)
            .count());
  }

  // Once a frame, at the guest's swap: every 10 s, the game's frame rate and
  // the lane's work.
  void FrameEnded() {
    const auto now = std::chrono::steady_clock::now();
    if (!frames_++) {
      report_start_ = now;
      return;
    }
    const double seconds = std::chrono::duration<double>(now - report_start_).count();
    if (seconds < 10.0) {
      return;
    }
    const uint32_t frames = frames_ - 1;
    REXGPU_INFO(
        "gpu: {:.1f} fps ({} frames in {:.1f} s); lane {}: {} jobs a frame, {} KB copied a frame, the "
        "command thread waited for it {} times, {} ms in all",
        frames / seconds, frames, seconds, state_ == kRunning ? "on" : "off",
        (pushed_ - reported_jobs_) / frames, ((copied_ - reported_copied_) >> 10) / frames, drains_,
        drain_wait_us_ / 1000);
    reported_jobs_ = pushed_;
    reported_copied_ = copied_;
    drains_ = drain_wait_us_ = 0;
    frames_ = 1;
    report_start_ = now;
  }

 private:
  struct Job {
    Call call;  // (nullptr: a copy)
    void* context;
    uint64_t argument;
    void* destination;
    const void* source;
    size_t size;
    bool watch;
  };
  enum State { kUnknown, kOff, kRunning };
  static constexpr int kSpins = 4000;  // (about 50 us before it sleeps)

  void Start() {
    state_ = REXCVAR_GET(crackdown_gpu_lane) ? kRunning : kOff;
    REXGPU_INFO("gpu lane: {}", state_ == kRunning ? "on (CPU 4)" : "off");
    if (state_ == kRunning) {
      pending_.reserve(4096);
      std::thread([this] { Main(); }).detach();
    }
  }

  void Push(const Job& job) {
    bool wake;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      pending_.push_back(job);
      ++pushed_;
      has_work_.store(true, std::memory_order_release);
      wake = sleeping_;
    }
    if (wake) {
      work_cv_.notify_one();
    }
  }

  void Main() {
#if defined(__PROSPERO__)
    // CPU 4, a core of its own (CPU 5, its other half, stays empty)
    scePthreadSetaffinity(pthread_self(), uint64_t(1) << 4);
#endif
    rex::ps5diag::RegisterThread("gpu lane");
    rex::memory::protect_after_unlock = true;
    std::vector<Job> batch;
    batch.reserve(4096);
    for (;;) {
      for (int spin = 0; spin < kSpins && !has_work_.load(std::memory_order_acquire); ++spin) {
        _mm_pause();
      }
      {
        std::unique_lock<std::mutex> lock(mutex_);
        while (pending_.empty()) {
          sleeping_ = true;
          work_cv_.wait(lock);
          sleeping_ = false;
        }
        batch.swap(pending_);
        has_work_.store(false, std::memory_order_relaxed);
      }
      for (size_t i = 0; i < batch.size();) {
        if (batch[i].watch) {
          // the watches in a row: their flags under one hold of the lock, then
          // their pages' protection (before the copies after them)
          {
            auto global_lock = rex::thread::global_critical_region::AcquireDirect();
            for (; i < batch.size() && batch[i].watch; ++i) {
              batch[i].call(batch[i].context, batch[i].argument);
            }
          }
          ProtectDeferred();
          continue;
        }
        const Job& job = batch[i++];
        if (job.call) {
          job.call(job.context, job.argument);
        } else {
          std::memcpy(job.destination, job.source, job.size);
        }
      }
      const uint64_t count = batch.size();
      batch.clear();
      bool notify;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        done_.fetch_add(count, std::memory_order_release);
        notify = drain_waiting_;
      }
      if (notify) {
        done_cv_.notify_all();
      }
    }
  }

  static void ProtectDeferred() {
    auto& protects = rex::memory::deferred_protects;
    if (protects.empty()) {
      return;
    }
    std::sort(protects.begin(), protects.end(),
              [](const auto& a, const auto& b) { return a.address < b.address; });
    size_t merged = 0;
    for (size_t i = 1; i < protects.size(); ++i) {
      auto& last = protects[merged];
      if (protects[i].access == last.access && protects[i].address <= last.address + last.length) {
        last.length = std::max(last.length, size_t(protects[i].address + protects[i].length - last.address));
      } else {
        protects[++merged] = protects[i];
      }
    }
    protects.resize(merged + 1);
    for (const auto& protect : protects) {
      rex::memory::Protect(protect.address, protect.length, protect.access);
    }
    protects.clear();
  }

  State state_ = kUnknown;
  std::mutex mutex_;
  std::condition_variable work_cv_, done_cv_;
  std::vector<Job> pending_;
  std::atomic<bool> has_work_{false};
  bool sleeping_ = false, drain_waiting_ = false;  // (under mutex_)
  uint64_t pushed_ = 0;                            // (under mutex_ when it changes)
  std::atomic<uint64_t> done_{0};
  // the report's
  uint64_t copied_ = 0, reported_jobs_ = 0, reported_copied_ = 0, drains_ = 0, drain_wait_us_ = 0;
  uint32_t frames_ = 0;
  std::chrono::steady_clock::time_point report_start_;
};

}  // namespace rex::graphics
''', encoding="utf-8")
edit("include/rex/graphics/flags.h", "REXCVAR_DECLARE(bool, async_shader_compilation);\n",
     "REXCVAR_DECLARE(bool, async_shader_compilation);\nREXCVAR_DECLARE(bool, crackdown_gpu_lane);\n")
edit("src/graphics/command_processor.cpp", """REXCVAR_DEFINE_BOOL(vsync, true, "GPU", "Enable vertical sync");
""", """REXCVAR_DEFINE_BOOL(vsync, true, "GPU", "Enable vertical sync");
REXCVAR_DEFINE_BOOL(crackdown_gpu_lane, true, "GPU",
                    "A second GPU thread for the guest memory copies, write watches and descriptor writes");
""")

# The command processor's guest-visible writes wait for the lane.
edit("src/graphics/command_processor.cpp", """#include <rex/graphics/flags.h>
#include <rex/graphics/shared_memory.h>
""", """#include <rex/graphics/flags.h>
#include <rex/graphics/gpu_lane.h>
#include <rex/graphics/shared_memory.h>
""")
edit("src/graphics/command_processor.cpp", """      memory::store_and_swap<uint32_t>(memory_->TranslatePhysical(read_ptr_writeback_ptr_),
                                       read_ptr_index_);
""", """      GpuLane::Get().Drain();  // [crackdown] (what the game sees: the lane's work done first)
      memory::store_and_swap<uint32_t>(memory_->TranslatePhysical(read_ptr_writeback_ptr_),
                                       read_ptr_index_);
""")
edit("src/graphics/command_processor.cpp", """      uint32_t mem_addr = scratch_addr + (scratch_reg * 4);
      memory::store_and_swap<uint32_t>(memory_->TranslatePhysical(mem_addr), value);
""", """      uint32_t mem_addr = scratch_addr + (scratch_reg * 4);
      GpuLane::Get().Drain();  // [crackdown]
      memory::store_and_swap<uint32_t>(memory_->TranslatePhysical(mem_addr), value);
""")
edit("src/graphics/command_processor.cpp", """  uint32_t cpu_mask = reader->ReadAndSwap<uint32_t>();
  for (int n = 0; n < 6; n++) {
""", """  uint32_t cpu_mask = reader->ReadAndSwap<uint32_t>();
  GpuLane::Get().Drain();  // [crackdown]
  for (int n = 0; n < 6; n++) {
""")
edit("src/graphics/command_processor.cpp", """  reg_val = GpuSwap(reg_val, endianness);
  memory::store(memory_->TranslatePhysical(mem_addr), reg_val);
""", """  reg_val = GpuSwap(reg_val, endianness);
  GpuLane::Get().Drain();  // [crackdown]
  memory::store(memory_->TranslatePhysical(mem_addr), reg_val);
""")
edit("src/graphics/command_processor.cpp", """  uint32_t write_addr = reader->ReadAndSwap<uint32_t>();
  for (uint32_t i = 0; i < count - 1; i++) {
""", """  uint32_t write_addr = reader->ReadAndSwap<uint32_t>();
  GpuLane::Get().Drain();  // [crackdown]
  for (uint32_t i = 0; i < count - 1; i++) {
""")
edit("src/graphics/command_processor.cpp", """      write_data = GpuSwap(write_data, endianness);
      memory::store(memory_->TranslatePhysical(write_reg_addr), write_data);
""", """      write_data = GpuSwap(write_data, endianness);
      GpuLane::Get().Drain();  // [crackdown]
      memory::store(memory_->TranslatePhysical(write_reg_addr), write_data);
""")
edit("src/graphics/command_processor.cpp", """  data_value = GpuSwap(data_value, endianness);
  memory::store(memory_->TranslatePhysical(address), data_value);
""", """  data_value = GpuSwap(data_value, endianness);
  GpuLane::Get().Drain();  // [crackdown]
  memory::store(memory_->TranslatePhysical(address), data_value);
""")

# Uploads: the watches and the copy on the lane, in that order (a write before the watch is armed
# is in the copy; one after it fires the watch).
edit("include/rex/graphics/shared_memory.h", """ protected:
  SharedMemory(memory::Memory& memory);
""", """ protected:
  // [crackdown] MakeRangeValid arms the watches on the GPU's second lane (from UploadRanges).
  bool watches_on_lane_ = false;

  SharedMemory(memory::Memory& memory);
""")
edit("src/graphics/shared_memory.cpp", """#include <rex/graphics/shared_memory.h>
""", """#include <rex/graphics/gpu_lane.h>
#include <rex/graphics/shared_memory.h>
""")
edit("src/graphics/shared_memory.cpp", """  if (memory_invalidation_callback_handle_) {
    for (const std::pair<uint32_t, uint32_t>& run : watch_runs_) {
      memory().EnablePhysicalMemoryAccessCallbacks(run.first << page_size_log2_,
                                                   run.second << page_size_log2_, true, false);
    }
  }
}
""", """  if (memory_invalidation_callback_handle_) {
    for (const std::pair<uint32_t, uint32_t>& run : watch_runs_) {
      if (watches_on_lane_) {
        GpuLane::Get().Watch(
            [](void* context, uint64_t run) {
              static_cast<memory::Memory*>(context)->EnablePhysicalMemoryAccessCallbacks(
                  uint32_t(run >> 32), uint32_t(run), true, false);
            },
            &memory(), (uint64_t(run.first << page_size_log2_) << 32) | (run.second << page_size_log2_));
        continue;
      }
      memory().EnablePhysicalMemoryAccessCallbacks(run.first << page_size_log2_,
                                                   run.second << page_size_log2_, true, false);
    }
  }
}
""")
edit("src/graphics/vulkan/shared_memory.cpp", """#include <rex/graphics/vulkan/shared_memory.h>
""", """#include <rex/graphics/gpu_lane.h>
#include <rex/graphics/vulkan/shared_memory.h>
""")
edit("src/graphics/vulkan/shared_memory.cpp", """      MakeRangeValid(upload_range_start << page_size_log2(), uint32_t(upload_buffer_size), false);
      // (hashed before the copy: a write no watch sees landing in between
      // then shows up as stale - one extra reload - instead of being missed)
      RecordPageHashes(upload_range_start, uint32_t(upload_buffer_size >> page_size_log2()));
      std::memcpy(upload_buffer_mapping,
                  memory().TranslatePhysical(upload_range_start << page_size_log2()),
                  upload_buffer_size);
""", """      // [crackdown] the watches and the copy on the GPU's second lane when it runs
      const bool lane = GpuLane::Get().Active();
      watches_on_lane_ = lane;
      MakeRangeValid(upload_range_start << page_size_log2(), uint32_t(upload_buffer_size), false);
      watches_on_lane_ = false;
      // (hashed before the copy: a write no watch sees landing in between
      // then shows up as stale - one extra reload - instead of being missed)
      if (lane) {
        RecordPageHashesOnLane(upload_range_start, uint32_t(upload_buffer_size >> page_size_log2()));
      } else {
        RecordPageHashes(upload_range_start, uint32_t(upload_buffer_size >> page_size_log2()));
      }
      if (lane) {
        GpuLane::Get().Copy(upload_buffer_mapping,
                            memory().TranslatePhysical(upload_range_start << page_size_log2()),
                            upload_buffer_size);
      } else {
        std::memcpy(upload_buffer_mapping,
                    memory().TranslatePhysical(upload_range_start << page_size_log2()),
                    upload_buffer_size);
      }
""")

# The watches' protection: on the lane, after the global lock is released (a page is only protected
# here once its flag says it's watched, and is read by the copy after that; the guest writing it in
# between doesn't fault and is in the copy).
edit("src/system/xmemory.cpp", """namespace rex::memory {
""", """namespace rex::memory {

thread_local bool protect_after_unlock = false;  // [crackdown] (gpu_lane.h)
struct DeferredProtect {
  uint8_t* address;
  size_t length;
  PageAccess access;
};
thread_local std::vector<DeferredProtect> deferred_protects;
""")
edit("src/system/xmemory.cpp", """  uint8_t* protect_base = membase_ + heap_base_;
  uint32_t protect_system_page_first = UINT32_MAX;
  auto global_lock = global_critical_region_.Acquire();
  for (uint32_t i = system_page_first; i <= system_page_last; ++i) {
    // Check if need to enable callbacks for the page and raise its protection.
""", """  uint8_t* protect_base = membase_ + heap_base_;
  uint32_t protect_system_page_first = UINT32_MAX;
  // [crackdown] the runs to protect, protected at the end
  static thread_local std::vector<std::pair<uint32_t, uint32_t>> protect_runs;
  protect_runs.clear();
  auto global_lock = global_critical_region_.Acquire();
  for (uint32_t i = system_page_first; i <= system_page_last; ++i) {
    // Check if need to enable callbacks for the page and raise its protection.
""")
edit("src/system/xmemory.cpp", """      if (protect_system_page_first != UINT32_MAX) {
        rex::memory::Protect(protect_base + protect_system_page_first * system_page_size_,
                             (i - protect_system_page_first) * system_page_size_, protect_access);
        protect_system_page_first = UINT32_MAX;
      }
    }
  }
  if (protect_system_page_first != UINT32_MAX) {
    rex::memory::Protect(protect_base + protect_system_page_first * system_page_size_,
                         (system_page_last + 1 - protect_system_page_first) * system_page_size_,
                         protect_access);
  }
}
""", """      if (protect_system_page_first != UINT32_MAX) {
        protect_runs.emplace_back(protect_system_page_first, i - protect_system_page_first);
        protect_system_page_first = UINT32_MAX;
      }
    }
  }
  if (protect_system_page_first != UINT32_MAX) {
    protect_runs.emplace_back(protect_system_page_first, system_page_last + 1 - protect_system_page_first);
  }
  if (protect_after_unlock) {
    // (the lane protects them once it has let go of the lock)
    for (const std::pair<uint32_t, uint32_t>& run : protect_runs) {
      deferred_protects.push_back({protect_base + run.first * system_page_size_,
                                   size_t(run.second) * system_page_size_, protect_access});
    }
    return;
  }
  for (const std::pair<uint32_t, uint32_t>& run : protect_runs) {
    rex::memory::Protect(protect_base + run.first * system_page_size_, run.second * system_page_size_,
                         protect_access);
  }
}
""")

# Texture descriptor sets: written on the lane, from copies of the writes (the command buffer's
# commands - its binds among them - are recorded at the submission, after the lane is drained).
edit("include/rex/graphics/vulkan/command_processor.h",
     """  uint32_t constants_dynamic_offsets_[SpirvShaderTranslator::kConstantBufferCount] = {};
""", """  uint32_t constants_dynamic_offsets_[SpirvShaderTranslator::kConstantBufferCount] = {};

  // [crackdown] Descriptor set writes for the GPU's second lane (gpu_lane.h), kept until it has
  // done them; never reallocated while it may read them. False: write them here.
  bool UpdateDescriptorSetsOnLane(uint32_t count, const VkWriteDescriptorSet* writes);
  static constexpr size_t kLaneDescriptorWrites = 16384, kLaneDescriptorImageInfos = 131072;
  std::vector<VkWriteDescriptorSet> lane_descriptor_writes_;
  std::vector<VkDescriptorImageInfo> lane_descriptor_image_info_;
""")
edit("src/graphics/vulkan/command_processor.cpp", """#include <rex/graphics/flags.h>
#include <rex/graphics/pipeline/shader/shader.h>
""", """#include <rex/graphics/flags.h>
#include <rex/graphics/gpu_lane.h>
#include <rex/graphics/pipeline/shader/shader.h>
""")
edit("src/graphics/vulkan/command_processor.cpp", """  // Write.
  if (write_descriptor_set_count) {
    dfn.vkUpdateDescriptorSets(device, write_descriptor_set_count, write_descriptor_sets.data(), 0,
                               nullptr);
  }
""", """  // Write.
  if (write_descriptor_set_count &&
      !UpdateDescriptorSetsOnLane(write_descriptor_set_count, write_descriptor_sets.data())) {
    dfn.vkUpdateDescriptorSets(device, write_descriptor_set_count, write_descriptor_sets.data(), 0,
                               nullptr);
  }
""")
edit("src/graphics/vulkan/command_processor.cpp", """uint32_t VulkanCommandProcessor::WriteTransientTextureBindings(
""", """bool VulkanCommandProcessor::UpdateDescriptorSetsOnLane(uint32_t count,
                                                        const VkWriteDescriptorSet* writes) {
  GpuLane& lane = GpuLane::Get();
  if (!lane.Active()) {
    return false;
  }
  size_t image_infos = 0;
  for (uint32_t i = 0; i < count; ++i) {
    if (!writes[i].pImageInfo || writes[i].pBufferInfo || writes[i].pTexelBufferView) {
      return false;
    }
    image_infos += writes[i].descriptorCount;
  }
  if (lane.Idle() || lane_descriptor_writes_.capacity() < kLaneDescriptorWrites) {
    // (all done: from the start again)
    lane.Drain();
    lane_descriptor_writes_.clear();
    lane_descriptor_image_info_.clear();
    lane_descriptor_writes_.reserve(kLaneDescriptorWrites);
    lane_descriptor_image_info_.reserve(kLaneDescriptorImageInfos);
  }
  if (lane_descriptor_writes_.size() + count > kLaneDescriptorWrites ||
      lane_descriptor_image_info_.size() + image_infos > kLaneDescriptorImageInfos) {
    lane.Drain();
    lane_descriptor_writes_.clear();
    lane_descriptor_image_info_.clear();
    if (count > kLaneDescriptorWrites || image_infos > kLaneDescriptorImageInfos) {
      return false;
    }
  }
  VkWriteDescriptorSet* const first = lane_descriptor_writes_.data() + lane_descriptor_writes_.size();
  for (uint32_t i = 0; i < count; ++i) {
    VkWriteDescriptorSet write = writes[i];
    const size_t at = lane_descriptor_image_info_.size();
    lane_descriptor_image_info_.insert(lane_descriptor_image_info_.end(), write.pImageInfo,
                                       write.pImageInfo + write.descriptorCount);
    write.pImageInfo = lane_descriptor_image_info_.data() + at;
    lane_descriptor_writes_.push_back(write);
  }
  static const ui::vulkan::VulkanDevice* lane_device;
  lane_device = GetVulkanDevice();
  lane.Run(
      [](void* context, uint64_t write_count) {
        lane_device->functions().vkUpdateDescriptorSets(
            lane_device->device(), uint32_t(write_count),
            static_cast<const VkWriteDescriptorSet*>(context), 0, nullptr);
      },
      first, count);
  return true;
}

uint32_t VulkanCommandProcessor::WriteTransientTextureBindings(
""")
# Before a submission's commands are recorded (and its uploads flushed): the lane's work done.
edit("src/graphics/vulkan/command_processor.cpp", """  bool is_closing_frame = is_swap && frame_open_;

  if (is_closing_frame) {
    texture_cache_->EndFrame();
""", """  // [crackdown] the GPU's second lane's copies and descriptor writes done first
  GpuLane::Get().Drain();

  bool is_closing_frame = is_swap && frame_open_;

  if (is_closing_frame) {
    texture_cache_->EndFrame();
""")
edit("src/graphics/vulkan/command_processor.cpp", """void VulkanCommandProcessor::ShutdownContext() {
  AwaitAllQueueOperationsCompletion();
""", """void VulkanCommandProcessor::ShutdownContext() {
  GpuLane::Get().Drain();  // [crackdown]
  AwaitAllQueueOperationsCompletion();
""")
edit("src/graphics/vulkan/command_processor.cpp", """                                       uint32_t frontbuffer_height) {
  SCOPE_profile_cpu_f("gpu");
  if (shared_memory_ && REXCVAR_GET(gpu_stale_check_pages_per_frame) > 0) {
""", """                                       uint32_t frontbuffer_height) {
  SCOPE_profile_cpu_f("gpu");
  GpuLane::Get().FrameEnded();  // [crackdown] (the frame rate, in the log every 10 s)
  if (shared_memory_ && REXCVAR_GET(gpu_stale_check_pages_per_frame) > 0) {
""")
# Its CPU: 4 (5 empty), left alone by the core layout, which keeps the other threads on 6-12.
edit("src/core/ps5_diag.cpp", """    if (!std::strcmp(e.what, "worker") || !std::strcmp(e.what, "recorder")) continue;
""", """    if (!std::strcmp(e.what, "worker") || !std::strcmp(e.what, "recorder") || !std::strcmp(e.what, "gpu lane"))
      continue;
""")
edit("include/rex/graphics/shared_memory.h", """  void RecordPageHashes(uint32_t page_first, uint32_t page_count);
""", """  void RecordPageHashes(uint32_t page_first, uint32_t page_count);
  // [crackdown] On the GPU's second lane, before its copy of them: all but the hot pages, which
  // CheckHotPagesIn compares on the command thread right away.
  void RecordPageHashesOnLane(uint32_t page_first, uint32_t page_count);
""")
edit("src/graphics/shared_memory.cpp", """uint32_t SharedMemory::CheckStalePages(uint32_t pages_per_frame) {
""", """void SharedMemory::RecordPageHashesOnLane(uint32_t page_first, uint32_t page_count) {
  auto push = [this](uint32_t from, uint32_t to) {
    if (to > from) {
      GpuLane::Get().Run(
          [](void* context, uint64_t run) {
            static_cast<SharedMemory*>(context)->RecordPageHashes(uint32_t(run >> 32), uint32_t(run));
          },
          this, (uint64_t(from) << 32) | (to - from));
    }
  };
  const uint32_t end = page_first + page_count;
  uint32_t run_first = page_first;
  for (uint32_t page = page_first; page < end; ++page) {
    if ((page >> 6) < hot_page_flags_.size() && (hot_page_flags_[page >> 6] & (uint64_t(1) << (page & 63)))) {
      push(run_first, page);
      RecordPageHashes(page, 1);
      run_first = page + 1;
    }
  }
  push(run_first, end);
}

uint32_t SharedMemory::CheckStalePages(uint32_t pages_per_frame) {
  GpuLane::Get().Drain();  // [crackdown] (the hashes it records)
""")

# Float constants copied a run at a time (UpdateBindings copied them 16 bytes at a time, with a bit
# scan for each).
edit("src/graphics/vulkan/command_processor.cpp", """#include <algorithm>
#include <array>
#include <cstdint>
""", """#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
""")
for base, which in (("000", "vertex"), ("256", "pixel")):
    edit("src/graphics/vulkan/command_processor.cpp",
         f"""        uint64_t float_constant_map_entry = current_float_constant_map_{which}_[i];
        uint32_t float_constant_index;
        while (rex::bit_scan_forward(float_constant_map_entry, &float_constant_index)) {{
          float_constant_map_entry &= ~(1ull << float_constant_index);
          std::memcpy(
              mapping,
              &regs[XE_GPU_REG_SHADER_CONSTANT_{base}_X + (i << 8) + (float_constant_index << 2)],
              sizeof(float) * 4);
          mapping += sizeof(float) * 4;
        }}
""", f"""        uint64_t float_constant_map_entry = current_float_constant_map_{which}_[i];
        while (float_constant_map_entry) {{
          // [crackdown] a run of constants at a time
          const uint32_t first = uint32_t(std::countr_zero(float_constant_map_entry));
          const uint32_t run = uint32_t(std::countr_one(float_constant_map_entry >> first));
          std::memcpy(mapping, &regs[XE_GPU_REG_SHADER_CONSTANT_{base}_X + (i << 8) + (first << 2)],
                      sizeof(float) * 4 * run);
          mapping += sizeof(float) * 4 * run;
          float_constant_map_entry &= run >= 64 ? 0 : ~(((uint64_t(1) << run) - 1) << first);
        }}
""")
print("gpu lane: guest memory copies, write watches, page hashes and texture descriptor writes on a second "
      "thread; float constants copied a run at a time")

# crackdown_002.log (60 FPS, the lane with its watches grouped): gameplay 40-60 FPS, the dips where
# 25-37 MB a frame of guest memory is uploaded. On the command thread:
# - 4.2% in CompleteOcclusionQueries invalidating the whole 64 KB readback buffer (all 8192 results)
#   at each check: now only the results from the first one it reads on (512 of them).
# - ~3% (GetLoggerRaw, kKnownRegisters) in the debug line for writes to registers the table doesn't
#   know, which the game makes all the time: gone.
# - Where the uploads come from, for the next log: what game writes invalidated, what the hot and
#   stale page checks did, and how many uploaded pages hadn't changed since their last upload.
edit("src/graphics/vulkan/command_processor.cpp", """  bool readback_invalidated = false;
  while (!pending_occlusion_queries_.empty()) {""", """  VkDeviceSize invalidated_begin = 0, invalidated_end = 0;  // [crackdown]
  while (!pending_occlusion_queries_.empty()) {""")
edit("src/graphics/vulkan/command_processor.cpp", """    if (!readback_invalidated && !(vulkan_device->memory_types().host_coherent &
                                   (uint32_t(1) << occlusion_query_readback_memory_type_))) {
      VkMappedMemoryRange memory_range = {};
      memory_range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
      memory_range.memory = occlusion_query_readback_memory_;
      memory_range.offset = 0;
      memory_range.size =
          std::min(rex::round_up(VkDeviceSize(sizeof(uint64_t) * kMaxOcclusionQueries),
                                 vulkan_device->properties().nonCoherentAtomSize),
                   occlusion_query_readback_memory_size_);
      dfn.vkInvalidateMappedMemoryRanges(device, 1, &memory_range);
      readback_invalidated = true;
    }""", """    const VkDeviceSize result_offset = VkDeviceSize(sizeof(uint64_t)) * query.host_index;
    if ((result_offset < invalidated_begin || result_offset >= invalidated_end) &&
        !(vulkan_device->memory_types().host_coherent &
          (uint32_t(1) << occlusion_query_readback_memory_type_))) {
      // [crackdown] the results from this one on (512 of them), not all 8192 at every check
      const VkDeviceSize atom =
          std::max(vulkan_device->properties().nonCoherentAtomSize, VkDeviceSize(1));
      VkMappedMemoryRange memory_range = {};
      memory_range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
      memory_range.memory = occlusion_query_readback_memory_;
      memory_range.offset = result_offset / atom * atom;
      memory_range.size =
          std::min(rex::round_up(result_offset + VkDeviceSize(sizeof(uint64_t) * 512), atom),
                   VkDeviceSize(occlusion_query_readback_memory_size_)) -
          memory_range.offset;
      dfn.vkInvalidateMappedMemoryRanges(device, 1, &memory_range);
      invalidated_begin = memory_range.offset;
      invalidated_end = memory_range.offset + memory_range.size;
    }""")
edit("src/graphics/command_processor.cpp", """  if (!kKnownRegisters[index]) {
    REXGPU_DEBUG("GPU: Write to unknown register ({:04X} = {:08X})", index, value);
  }
""", """  // [crackdown] (no debug line for unknown registers: the game writes them all the time)
""")

# The upload counters (gpu_lane.h has them; its frame rate line reports them).
edit("include/rex/graphics/gpu_lane.h", """  // Once a frame, at the guest's swap: every 10 s, the game's frame rate and
  // the lane's work.""", """  // Where uploads come from (any thread): bytes invalidated by guest writes and
  // by the hot and stale page checks, pages hashed at upload and those of them
  // unchanged since they were last uploaded.
  std::atomic<uint64_t> invalidated_by_writes{0}, invalidated_by_checks{0}, pages_hashed{0},
      pages_unchanged{0};

  // Once a frame, at the guest's swap: every 10 s, the game's frame rate and
  // the lane's work.""")
edit("include/rex/graphics/gpu_lane.h", """        drain_wait_us_ / 1000);
    reported_jobs_ = pushed_;""", """        drain_wait_us_ / 1000);
    const uint64_t hashed = pages_hashed.exchange(0), unchanged = pages_unchanged.exchange(0);
    REXGPU_INFO(
        "gpu uploads: {} KB a frame invalidated by game writes, {} KB by the page checks; {} pages "
        "uploaded a frame, {}% of them unchanged since their last upload",
        (invalidated_by_writes.exchange(0) >> 10) / frames, (invalidated_by_checks.exchange(0) >> 10) / frames,
        hashed / frames, hashed ? unchanged * 100 / hashed : 0);
    reported_jobs_ = pushed_;""")
edit("src/graphics/shared_memory.cpp", """std::pair<uint32_t, uint32_t> SharedMemory::MemoryInvalidationCallbackThunk(
    void* context_ptr, uint32_t physical_address_start, uint32_t length, bool exact_range) {
  return reinterpret_cast<SharedMemory*>(context_ptr)
      ->MemoryInvalidationCallback(physical_address_start, length, exact_range);
}""", """namespace {
thread_local bool invalidation_from_write = false;  // [crackdown]
}  // namespace

std::pair<uint32_t, uint32_t> SharedMemory::MemoryInvalidationCallbackThunk(
    void* context_ptr, uint32_t physical_address_start, uint32_t length, bool exact_range) {
  // [crackdown] (counted: a guest write's, not a page check's)
  GpuLane::Get().invalidated_by_writes.fetch_add(length, std::memory_order_relaxed);
  invalidation_from_write = true;
  const auto result = reinterpret_cast<SharedMemory*>(context_ptr)
      ->MemoryInvalidationCallback(physical_address_start, length, exact_range);
  invalidation_from_write = false;
  return result;
}""")
edit("src/graphics/shared_memory.cpp", """  length = std::min(length, kBufferSize - physical_address_start);
  uint32_t physical_address_last = physical_address_start + (length - 1);

  uint32_t page_first = physical_address_start >> page_size_log2_;""", """  length = std::min(length, kBufferSize - physical_address_start);
  uint32_t physical_address_last = physical_address_start + (length - 1);
  if (!invalidation_from_write) {
    // [crackdown] (the page checks' - a guest write's comes through the thunk, counted there)
    GpuLane::Get().invalidated_by_checks.fetch_add(length, std::memory_order_relaxed);
  }

  uint32_t page_first = physical_address_start >> page_size_log2_;""")
edit("src/graphics/shared_memory.cpp", """    const uint64_t hash =
        XXH3_64bits(memory_.TranslatePhysical((page << page_size_log2_) & (kBufferSize - 1)), page_size);
    page_hashes_[page] = hash ? hash : 1;
  }""", """    const uint64_t hash =
        XXH3_64bits(memory_.TranslatePhysical((page << page_size_log2_) & (kBufferSize - 1)), page_size);
    // [crackdown] (counted: an upload of a page that hadn't changed)
    if (page_hashes_[page] == (hash ? hash : 1)) {
      GpuLane::Get().pages_unchanged.fetch_add(1, std::memory_order_relaxed);
    }
    GpuLane::Get().pages_hashed.fetch_add(1, std::memory_order_relaxed);
    page_hashes_[page] = hash ? hash : 1;
  }""")
print("gpu: occlusion results invalidated where they're read, no unknown-register debug line, upload counters")
