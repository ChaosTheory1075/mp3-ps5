#!/usr/bin/env python3
"""Adapt a gta4-ps5 checkout to build Max Payne 3 instead of GTA IV.

    patch_framework.py <gta4-ps5 checkout> [--host]

- glue/CMakeLists.txt adds mp3-recomp (copied in by the workflow) instead of
  gta4-recomp.
- src/graphics/CMakeLists.txt: on the PS5, the generic Xenos renderer
  (rexgpu-xenos) is built as a static library and linked in, and GTA IV's own
  renderer (rexgpu-gta4-native, its shader caches and FSR archive) is left out.
- src/filesystem/devices/disc_image_device.cpp includes direct_io.h on every
  non-Windows host (only the PS5 build had it).

Each edit checks that it applied, so a change upstream fails the build here
instead of somewhere later.
"""
import pathlib
import os
import re
import sys


def edit(path, old, new, count=1):
    text = path.read_text()
    if new in text:
        print(f"already  {path}")
        return
    if text.count(old) != count:
        if os.environ.get("PATCH_KEEP_GOING"):  # (to list every mismatch at once)
            print(f"MISMATCH {path.name}: {old[:150]!r}")
            return
        sys.exit(f"patch_framework: expected {count} of {old!r} in {path}, found {text.count(old)}")
    path.write_text(text.replace(old, new))
    print(f"patched  {path}")


def main():
    fw = pathlib.Path(sys.argv[1])
    sdk = fw / "glue" / "rexglue-sdk-main"

    edit(fw / "glue" / "CMakeLists.txt",
         'add_subdirectory("${REXSDK_DIR}/gta4-recomp" "${CMAKE_CURRENT_BINARY_DIR}/gta4-recomp")',
         'add_subdirectory("${REXSDK_DIR}/mp3-recomp" "${CMAKE_CURRENT_BINARY_DIR}/mp3-recomp")')

    gfx = sdk / "src" / "graphics" / "CMakeLists.txt"
    edit(gfx,
         "if(NOT PS5)\nadd_library(rexgpu-xenos SHARED\n",
         "if(PS5)\n"
         "# [mp3] the generic Xenos renderer, linked into the PS5 program\n"
         "add_library(rexgpu-xenos STATIC\n"
         "    plugin_main.cpp\n"
         "    ${REXGPU_XENOS_SOURCES}\n"
         ")\n"
         "add_library(rex::gpu-xenos ALIAS rexgpu-xenos)\n"
         "rexgpu_configure_plugin(rexgpu-xenos gpu-xenos)\n"
         "else()\n"
         "add_library(rexgpu-xenos SHARED\n")
    # GTA IV's renderer block: off for this game.
    edit(gfx,
         "if(REXGLUE_USE_VULKAN)\n    if(REXGLUE_BUILD_TESTS)\n",
         "if(REXGLUE_USE_VULKAN AND NOT MP3_NO_GTA4_NATIVE)\n    if(REXGLUE_BUILD_TESTS)\n")

    # XamSwapDisc reaches the app (mp3_app.cpp: disc 2 by starting again on
    # its files); without one, the stub as before.
    xam_info = sdk / "src" / "kernel" / "xam" / "xam_info.cpp"
    edit(xam_info,
         "u32 XamSwapDisc_entry(u32 disc_number) {\n",
         "extern \"C\" __attribute__((weak)) void rex_app_swap_disc(uint32_t disc_number);\n\n"
         "u32 XamSwapDisc_entry(u32 disc_number) {\n"
         "  REXKRNL_INFO(\"XamSwapDisc({})\", (uint32_t)disc_number);\n"
         "  if (rex_app_swap_disc) rex_app_swap_disc(disc_number);\n")
    # The game's file reads, in the log (the telemetry was only printed on
    # Android): every 10 s while it reads, how much and how long a read took,
    # for reports of slow loading.
    xio = sdk / "src" / "kernel" / "xboxkrnl" / "xboxkrnl_io.cpp"
    edit(xio,
         "  if (now - last > 2000000000ll && t.last_report_ns.compare_exchange_strong(last, now)) {\n",
         "  if (now - last > 10000000000ll && t.last_report_ns.compare_exchange_strong(last, now)) {\n")
    edit(xio,
         "                        (long long)peak);\n#endif\n",
         "                        (long long)peak);\n#endif\n"
         "    if (last) {  // [mp3]\n"
         "      const double secs = double(now - last) / 1e9;\n"
         "      REXKRNL_INFO(\"mp3 io: {:.0f} reads/s ({} async), {:.1f} MB/s, {:.0f} KB a read, {:.2f} ms a read, \"\n"
         "                   \"{} at once at most\",\n"
         "                   reads / secs, async_reads, total / 1e6 / secs, reads ? total / 1024.0 / reads : 0.0,\n"
         "                   reads ? service / 1e6 / reads : 0.0, peak);\n"
         "    }\n")
    # XGetLanguage returned English whatever user_language said (the Xbox 360's
    # console language, which the game's text and trophies follow): the
    # menu's LANGUAGE setting sets it (mp3_menu.cpp).
    edit(xam_info,
         "#include <rex/system/kernel_state.h>\n",
         "#include <rex/system/flags.h>\n#include <rex/system/kernel_state.h>\n")
    edit(xam_info,
         "u32 XGetLanguage_entry() {\n",
         "u32 XGetLanguage_entry() {\n"
         "  if (const uint32_t language = REXCVAR_GET(user_language); language >= 1 && language <= 12)\n"
         "    return language;\n")

    # HD texture replacement in the Vulkan texture cache (ci/fw-files/
    # src/graphics/vulkan/hd_textures.cpp): its members, and three hooks.
    files = pathlib.Path(__file__).resolve().parent / "fw-files"
    for f in files.rglob("*"):
        if f.is_file():
            dst = sdk / f.relative_to(files)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(f.read_bytes())
            print(f"copied   {dst}")
    edit(gfx,
         "    vulkan/texture_cache.cpp\n",
         "    vulkan/texture_cache.cpp\n    vulkan/hd_textures.cpp\n")
    # The graphics system (its presenter), for the app: the menu's UPSCALING
    # setting goes to the presenter, which took present_effect at its start,
    # before the menu (mp3_app.cpp).
    edit(sdk / "include" / "rex" / "rex_app.h",
         "  ui::Window* window() const { return window_.get(); }\n",
         "  ui::Window* window() const { return window_.get(); }\n"
         "  system::IGraphicsSystem* graphics_system() const { return config_.graphics.get(); }  // [mp3]\n")
    # SMAA 1x as a swap post effect (swap_post_effect smaa: ci/fw-files/src/graphics/vulkan/
    # mp3_smaa.cpp, shaders from tools/smaa). It runs where FXAA does, on the same gamma-corrected
    # source image, before the presenter's upscaling (FSR 1): SMAA cleans the edges with less blur
    # than FXAA, so the upscaler doesn't enlarge the jaggies. FXAA stays the fallback.
    edit(sdk / "include" / "rex" / "graphics" / "command_processor.h",
         "    kFxaaExtreme,\n  };\n",
         "    kFxaaExtreme,\n    kSmaa,  // [mp3]\n  };\n")
    edit(sdk / "src" / "graphics" / "graphics_system.cpp",
         "    .allowed({\"none\", \"fxaa\", \"fxaa_extreme\"})\n",
         "    .allowed({\"none\", \"fxaa\", \"fxaa_extreme\", \"smaa\"})  // [mp3]\n")
    edit(sdk / "src" / "graphics" / "graphics_system.cpp",
         "  if (lowered == \"fxaa_extreme\" || lowered == \"extreme\") {\n",
         "  if (lowered == \"smaa\") {  // [mp3]\n"
         "    return rex::graphics::CommandProcessor::SwapPostEffect::kSmaa;\n"
         "  }\n"
         "  if (lowered == \"fxaa_extreme\" || lowered == \"extreme\") {\n")
    vcph_smaa = sdk / "include" / "rex" / "graphics" / "vulkan" / "command_processor.h"
    edit(vcph_smaa,
         "  void BindExternalComputePipeline(VkPipeline pipeline);\n",
         "  void BindExternalComputePipeline(VkPipeline pipeline);\n"
         "  // [mp3] SMAA (mp3_smaa.cpp): false if unavailable (FXAA then).\n"
         "  bool Mp3SmaaRecord(VkImageView color_view, VkImage dest_image, VkImageView dest_view,\n"
         "                     uint32_t width, uint32_t height, uint32_t frame_index);\n"
         "  bool Mp3SmaaInitialize();\n"
         "  void Mp3SmaaShutdown();\n")
    edit(vcph_smaa,
         "  VkImageLayout swap_fxaa_source_layout_ = VK_IMAGE_LAYOUT_UNDEFINED;\n",
         "  VkImageLayout swap_fxaa_source_layout_ = VK_IMAGE_LAYOUT_UNDEFINED;\n"
         "  struct Mp3Smaa;\n"
         "  Mp3Smaa* mp3_smaa_ = nullptr;  // [mp3] created on first use\n")
    vcp_smaa = sdk / "src" / "graphics" / "vulkan" / "command_processor.cpp"
    edit(vcp_smaa,
         "        bool use_fxaa = swap_post_effect == SwapPostEffect::kFxaa ||\n"
         "                        swap_post_effect == SwapPostEffect::kFxaaExtreme;\n",
         "        bool use_fxaa = swap_post_effect == SwapPostEffect::kFxaa ||\n"
         "                        swap_post_effect == SwapPostEffect::kFxaaExtreme ||\n"
         "                        swap_post_effect == SwapPostEffect::kSmaa;  // [mp3] FXAA's source image\n")
    edit(vcp_smaa,
         "            BindExternalComputePipeline(swap_post_effect == SwapPostEffect::kFxaaExtreme\n"
         "                                            ? swap_fxaa_extreme_pipeline_\n"
         "                                            : swap_fxaa_pipeline_);\n"
         "            deferred_command_buffer_.CmdVkDispatch(group_count_x, group_count_y, 1);\n",
         "            if (swap_post_effect != SwapPostEffect::kSmaa ||\n"
         "                !Mp3SmaaRecord(swap_fxaa_source_image_view_, vulkan_context.image(),\n"
         "                               vulkan_context.image_view(), guest_output_width,\n"
         "                               guest_output_height, swap_frame_index)) {  // [mp3]\n"
         "              BindExternalComputePipeline(swap_post_effect == SwapPostEffect::kFxaaExtreme\n"
         "                                              ? swap_fxaa_extreme_pipeline_\n"
         "                                              : swap_fxaa_pipeline_);\n"
         "              deferred_command_buffer_.CmdVkDispatch(group_count_x, group_count_y, 1);\n"
         "            }\n")
    edit(vcp_smaa,
         "void VulkanCommandProcessor::ShutdownContext() {\n"
         "  GpuLane::Get().Drain();  // [crackdown]\n"
         "  AwaitAllQueueOperationsCompletion();\n",
         "void VulkanCommandProcessor::ShutdownContext() {\n"
         "  GpuLane::Get().Drain();  // [crackdown]\n"
         "  AwaitAllQueueOperationsCompletion();\n"
         "  Mp3SmaaShutdown();  // [mp3]\n")
    edit(gfx,
         "    vulkan/texture_cache.cpp\n    vulkan/hd_textures.cpp\n",
         "    vulkan/texture_cache.cpp\n    vulkan/hd_textures.cpp\n    vulkan/mp3_smaa.cpp\n")
    tc_h = sdk / "include" / "rex" / "graphics" / "vulkan" / "texture_cache.h"
    edit(tc_h,
         "class VulkanTextureCache final : public TextureCache {\n",
         "struct HdReplacement;\nstruct HdReplacer;\nstruct HdDds;\n\n"
         "class VulkanTextureCache final : public TextureCache {\n")
    edit(tc_h,
         "    VkImage image() const { return image_; }\n",
         "    VkImage image() const { return image_; }\n\n"
         "    // HD textures (hd_textures.cpp): the replacement this texture's views come from.\n"
         "    std::shared_ptr<HdReplacement> hd_replacement;\n"
         "    uint64_t hd_pending = 0;  // (the replacement being read in)\n"
         "    uint64_t hd_last_load = 0;  // (submission)\n"
         "    uint8_t hd_fast_reloads = 0;\n"
         "    friend class VulkanTextureCache;\n")
    edit(tc_h,
         "  std::pair<uint64_t, uint64_t> scaled_resolve_last_written_range_{0, 0};\n};\n",
         "  std::pair<uint64_t, uint64_t> scaled_resolve_last_written_range_{0, 0};\n\n"
         "  // HD textures (hd_textures.cpp)\n"
         "  HdReplacer* Hd() const;\n"
         "  void HdBeginSubmission();\n"
         "  void HdAfterLoad(VulkanTexture& texture, bool load_base);\n"
         "  std::shared_ptr<HdReplacement> HdLoad(uint64_t hash);\n"
         "  std::shared_ptr<HdReplacement> HdUpload(uint64_t hash, HdDds& dds);\n"
         "  void HdPoll();\n"
         "  VkImageView HdView(VulkanTexture& texture, bool is_signed, uint32_t host_swizzle, bool is_array) const;\n"
         "  mutable HdReplacer* hd_ = nullptr;\n"
         "  mutable bool hd_disabled_ = false;\n};\n")
    tc = sdk / "src" / "graphics" / "vulkan" / "texture_cache.cpp"
    edit(tc,
         "  TextureCache::RequestTextures(used_texture_mask);\n",
         "  HdPoll();\n  TextureCache::RequestTextures(used_texture_mask);\n")
    edit(tc,
         "  TextureCache::BeginSubmission(new_submission_index);\n",
         "  TextureCache::BeginSubmission(new_submission_index);\n  HdBeginSubmission();\n")
    edit(tc,
         "    copy_region.imageExtent.depth = std::max(depth >> level, UINT32_C(1));\n  }\n\n  return true;\n}\n",
         "    copy_region.imageExtent.depth = std::max(depth >> level, UINT32_C(1));\n  }\n\n"
         "  HdAfterLoad(vulkan_texture, load_base);\n  return true;\n}\n")
    edit(tc,
         "VkImageView VulkanTextureCache::VulkanTexture::GetView(bool is_signed, uint32_t host_swizzle,\n"
         "                                                       bool is_array) {\n",
         "VkImageView VulkanTextureCache::VulkanTexture::GetView(bool is_signed, uint32_t host_swizzle,\n"
         "                                                       bool is_array) {\n"
         "  if (hd_replacement) {\n"
         "    const auto& cache = static_cast<const VulkanTextureCache&>(texture_cache());\n"
         "    if (VkImageView view = cache.HdView(*this, is_signed, host_swizzle, is_array)) return view;\n"
         "  }\n")

    # REX_DUMP_IMAGE=<file>: codegen writes the loaded (decrypted) image
    # there, for tools/find_rpf_key.py (the archives' AES key is in it).
    bv = sdk / "src" / "codegen" / "binary_view.cpp"
    edit(bv,
         "  return view;\n}\n",
         "  if (const char* dump = std::getenv(\"REX_DUMP_IMAGE\")) {\n"
         "    if (std::FILE* f = std::fopen(dump, \"wb\")) {\n"
         "      for (const auto& s : view.sections_) {\n"
         "        if (!s.data || s.baseAddress < view.baseAddress_) continue;\n"
         "        std::fseek(f, long(s.baseAddress - view.baseAddress_), SEEK_SET);\n"
         "        std::fwrite(s.data, 1, s.size, f);\n"
         "      }\n"
         "      std::fclose(f);\n"
         "    }\n"
         "  }\n"
         "  return view;\n}\n")
    edit(bv, "#include <limits>\n", "#include <cstdio>\n#include <cstdlib>\n#include <limits>\n")

    edit(sdk / "src" / "filesystem" / "devices" / "disc_image_device.cpp",
         "#include <rex/platform.h>\n#if REX_PLATFORM_PS5\n#include <rex/filesystem/direct_io.h>\n#endif\n",
         "#include <rex/platform.h>\n#include <rex/filesystem/direct_io.h>\n")

    # The bring-up counters (rex/ps5_diag.h) are counted outside PS5-only code
    # too; elsewhere than the PS5 nothing defines them (ps5_diag.cpp is PS5's).
    (sdk / "src" / "core" / "ps5_diag_host.cpp").write_text(
        "// [mp3] rex/ps5_diag.h's counters off the PS5 (ps5_diag.cpp is PS5-only).\n"
        "#include <rex/ps5_diag.h>\n\n"
        "namespace rex::ps5diag {\n"
        "std::atomic<uint64_t> counters[kCount];\n"
        "uintptr_t guest_arena_lo = 0, guest_arena_hi = 0;\n"
        "}  // namespace rex::ps5diag\n")
    edit(sdk / "src" / "core" / "CMakeLists.txt",
         "    target_sources(rexcore PRIVATE ps5_libc_extras.c ps5_diag.cpp)\n",
         "    target_sources(rexcore PRIVATE ps5_libc_extras.c ps5_diag.cpp)\n"
         "else()\n"
         "    target_sources(rexcore PRIVATE ps5_diag_host.cpp)  # [mp3]\n"
         "endif()\n"
         "if(PS5)\n")

    # PS5 audio: a sample the game's mixer left NaN / infinite (std::clamp lets
    # NaN through, and its int16 cast is garbage: loud crackle) is played as 0,
    # and every 10 s with any, the count and the loudest sample are logged.
    drv = sdk / "src" / "audio" / "ps5" / "ps5_audio_driver.cpp"
    edit(drv,
         "  bool started = false, in_gap = false;\n  uint32_t gaps = 0, silent = 0, played = 0;\n",
         "  bool started = false, in_gap = false;\n  uint32_t gaps = 0, silent = 0, played = 0;\n"
         "  uint32_t bad = 0, clipped = 0;  // [mp3]\n  float peak = 0.0f;\n")
    edit(drv,
         "      gaps = silent = played = 0;\n",
         "      if (bad || clipped) {\n"
         "        REXAPU_INFO(\"PS5 audio: {} NaN/inf samples (played as 0), {} clipped, peak {:.2f} in 10 s\", bad,\n"
         "                    clipped, peak);\n"
         "      }\n"
         "      bad = clipped = 0, peak = 0.0f;\n"
         "      gaps = silent = played = 0;\n")
    edit(drv,
         "        float s = std::clamp(stereo[i], -1.0f, 1.0f);\n",
         "        float s = stereo[i];\n"
         "        if (!std::isfinite(s)) s = 0.0f, ++bad;\n"
         "        peak = std::max(peak, std::fabs(s));\n"
         "        if (std::fabs(s) > 1.0f) ++clipped;\n"
         "        s = std::clamp(s, -1.0f, 1.0f);\n")
    edit(drv, "#include <algorithm>\n", "#include <algorithm>\n#include <cmath>\n")

    # Audio diagnostics: what the XMA decoder did (contexts decoded, decode
    # errors, output buffers full, input run dry) every 10 s, and the PS5
    # output's first 90 s of sound in logs/mp3_audio.raw (48 kHz s16 stereo;
    # only with logs/capture.txt)
    # with a count of frames identical to the one before (a stuck voice
    # buzzes) -- the noise, to look at instead of guessing.
    xc = sdk / "src" / "audio" / "xma_context.cpp"
    edit(xc,
         "namespace rex::audio {\n\nusing stream::BitStream;\n",
         "namespace rex::audio {\n\n"
         "// [mp3] decoded, errors, output full, input dry, kicks of a disabled context\n"
         "std::atomic<uint64_t> mp3_xma_stats[5];\n"
         "static void Mp3XmaReport() {\n"
         "  static std::atomic<int64_t> last{0};\n"
         "  const int64_t now = std::chrono::duration_cast<std::chrono::seconds>(\n"
         "      std::chrono::steady_clock::now().time_since_epoch()).count();\n"
         "  int64_t prev = last.load();\n"
         "  if (now - prev < 10 || !last.compare_exchange_strong(prev, now)) return;\n"
         "  REXAPU_INFO(\"XMA 10 s: {} decodes, {} errors, {} output full, {} input dry\",\n"
         "              mp3_xma_stats[0].exchange(0), mp3_xma_stats[1].exchange(0),\n"
         "              mp3_xma_stats[2].exchange(0), mp3_xma_stats[3].exchange(0));\n"
         "}\n\n"
         "using stream::BitStream;\n")
    edit(xc, "#include <algorithm>\n#include <cstring>\n",
         "#include <algorithm>\n#include <atomic>\n#include <chrono>\n#include <cstring>\n")
    edit(xc,
         "  if (minimum_subframe_decode_count > remaining_subframe_blocks_in_output_buffer_) {\n",
         "  Mp3XmaReport();\n"
         "  if (minimum_subframe_decode_count > remaining_subframe_blocks_in_output_buffer_) {\n"
         "    ++mp3_xma_stats[2];\n")
    edit(xc,
         "    Decode(&data);\n    Consume(&output_rb, &data);\n",
         "    Decode(&data);\n    Consume(&output_rb, &data);\n    ++mp3_xma_stats[0];\n")
    edit(xc,
         "    if (!data.IsAnyInputBufferValid() || data.error_status == 4) {\n      break;\n",
         "    if (!data.IsAnyInputBufferValid() || data.error_status == 4) {\n"
         "      ++mp3_xma_stats[data.error_status == 4 ? 1 : 3];\n      break;\n")

    drv = sdk / "src" / "audio" / "ps5" / "ps5_audio_driver.cpp"
    edit(drv,
         "  uint32_t bad = 0, clipped = 0;  // [mp3]\n",
         "  uint32_t bad = 0, clipped = 0, repeats = 0;  // [mp3]\n"
         "  std::vector<int16_t> previous(kChannelSamples * 2, 0);\n"
         "  FILE* capture = nullptr;\n  uint64_t captured = 0;  // (frames)\n")
    edit(drv,
         "      if (bad || clipped) {\n",
         "      REXAPU_INFO(\"PS5 audio 10 s: {} frames repeated exactly, peak {:.2f}\", repeats, peak);\n"
         "      repeats = 0;\n"
         "      if (bad || clipped) {\n")
    edit(drv,
         "    sceAudioOutOutput(port_, pcm);\n  }\n",
         "    if (frame) {\n"
         "      bool nonzero = false;\n"
         "      for (uint32_t i = 0; i < kChannelSamples * 2 && !nonzero; ++i) nonzero = pcm[i] != 0;\n"
         "      if (nonzero && std::memcmp(previous.data(), pcm, sizeof(pcm)) == 0) ++repeats;\n"
         "      std::memcpy(previous.data(), pcm, sizeof(pcm));\n"
         "      // (only with logs/capture.txt, as the frame captures)\n"
         "      static const bool capture_wanted = [] {\n"
         "        std::FILE* f = std::fopen(\"/app0/logs/capture.txt\", \"rb\");\n"
         "        if (f) std::fclose(f);\n"
         "        return f != nullptr;\n"
         "      }();\n"
         "      if (!capture && !captured && !capture_wanted) captured = 1;\n"
         "      if (!capture && !captured) {\n"
         "        capture = std::fopen(\"/app0/logs/mp3_audio.raw\", \"wb\");\n"
         "        if (!capture) captured = 1;\n"
         "      }\n"
         "      if (capture) {\n"
         "        std::fwrite(pcm, sizeof(pcm), 1, capture);\n"
         "        if (++captured >= uint64_t(kFrequency) * 90 / kChannelSamples) {\n"
         "          std::fclose(capture);\n          capture = nullptr;\n"
         "          REXAPU_INFO(\"PS5 audio: 90 s captured to logs/mp3_audio.raw\");\n"
         "        }\n"
         "      }\n"
         "    }\n"
         "    sceAudioOutOutput(port_, pcm);\n  }\n"
         "  if (capture) std::fclose(capture);\n")
    edit(drv, "#include <cmath>\n", "#include <cmath>\n#include <cstdio>\n#include <vector>\n")

    # XMA decoder: where the framework's decoder differs from Xenia canary's
    # (xma_context_new.cc), each feeding the codec a wrong frame. Gameplay had
    # 30-50% of decode passes end in error_status 4 and the capture a 62.5 Hz
    # buzz (one 512-sample frame at 32 kHz):
    # - a packet that only continues an earlier frame was skipped by moving to
    #   the next packet in memory, which in an interleaved (multi-stream:
    #   cutscene and gameplay surround) buffer belongs to another stream;
    #   Xenia follows the packet's own skip count.
    # - a packet past the end of the current buffer was always taken as the
    #   next buffer's first packet; it is (index - count) of the next buffer.
    # - the search for the next frame stopped at the end of the current
    #   buffer instead of going on into the next one.
    # - Work set error_status 4 when one pass made no progress (waiting for
    #   the game's next buffer is not an error) and dropped the rest of a
    #   decoded frame on an error; Xenia stops without an error and drains
    #   the frame first.
    # Plus a count per error site, logged with the 10 s report.
    edit(xc,
         "std::atomic<uint64_t> mp3_xma_stats[5];\n",
         "std::atomic<uint64_t> mp3_xma_stats[5];\n"
         "std::atomic<uint64_t> mp3_xma_err[16];  // per error_status = 4 site in Decode\n"
         "std::atomic<uint64_t> mp3_xma_stale;  // kicks that began with error_status 4\n")
    edit(xc,
         "              mp3_xma_stats[2].exchange(0), mp3_xma_stats[3].exchange(0));\n",
         "              mp3_xma_stats[2].exchange(0), mp3_xma_stats[3].exchange(0));\n"
         "  std::string sites;\n"
         "  for (auto& e : mp3_xma_err) sites += std::to_string(e.exchange(0)) + \" \";\n"
         "  REXAPU_INFO(\"XMA errors by site: {}| begun in error {}, stalled {}\", sites,\n"
         "              mp3_xma_stale.exchange(0), mp3_xma_stats[4].exchange(0));\n")
    edit(xc, "#include <chrono>\n#include <cstring>\n",
         "#include <chrono>\n#include <cstring>\n#include <string>\n")
    edit(xc,
         "  memory::RingBuffer output_rb = PrepareOutputRingBuffer(&data);\n",
         "  memory::RingBuffer output_rb = PrepareOutputRingBuffer(&data);\n"
         "  if (data.error_status == 4) ++mp3_xma_stale;\n")
    edit(xc,
         "    // Invalid packets must not leave the decoder worker spinning forever.\n"
         "    if (data.IsAnyInputBufferValid() && data.error_status != 4 &&\n"
         "        data.input_buffer_read_offset == old_offset && data.current_buffer == old_buffer &&\n"
         "        data.IsAnyInputBufferValid() == old_valid &&\n"
         "        current_frame_remaining_subframes_ == old_remaining &&\n"
         "        remaining_subframe_blocks_in_output_buffer_ == old_capacity) {\n"
         "      data.error_status = 4;\n"
         "      handoff::Record(\"xma-error\", id_, {old_offset, old_buffer}, \"decode-no-progress\");\n"
         "    }\n"
         "    if (!data.IsAnyInputBufferValid() || data.error_status == 4) {\n"
         "      ++mp3_xma_stats[data.error_status == 4 ? 1 : 3];\n      break;\n    }\n",
         "    // [mp3] As Xenia: finish delivering a decoded frame before stopping\n"
         "    // on an error or on running out of input, and stop without an error\n"
         "    // when a pass made no progress (waiting for the game's next buffer).\n"
         "    if ((!data.IsAnyInputBufferValid() || data.error_status == 4) &&\n"
         "        current_frame_remaining_subframes_ == 0) {\n"
         "      ++mp3_xma_stats[data.error_status == 4 ? 1 : 3];\n      break;\n    }\n"
         "    if (data.input_buffer_read_offset == old_offset && data.current_buffer == old_buffer &&\n"
         "        data.IsAnyInputBufferValid() == old_valid &&\n"
         "        current_frame_remaining_subframes_ == old_remaining &&\n"
         "        remaining_subframe_blocks_in_output_buffer_ == old_capacity) {\n"
         "      ++mp3_xma_stats[4];\n      break;\n    }\n")
    # The next buffer's packet (index - count), within that buffer's length.
    edit(xc,
         "  const uint8_t next_buffer_index = data->current_buffer ^ 1;\n"
         "  if (!data->IsInputBufferValid(next_buffer_index)) {\n"
         "    return nullptr;\n  }\n",
         "  const uint8_t next_buffer_index = data->current_buffer ^ 1;\n"
         "  if (!data->IsInputBufferValid(next_buffer_index)) {\n"
         "    return nullptr;\n  }\n"
         "  next_packet_index -= current_input_packet_count;  // [mp3]\n"
         "  if (next_packet_index >= data->GetInputBufferPacketCount(next_buffer_index)) {\n"
         "    return nullptr;\n  }\n")
    edit(xc,
         "  return memory()->TranslatePhysical(next_buffer_address);\n",
         "  return memory()->TranslatePhysical(next_buffer_address) + next_packet_index * kBytesPerPacket;\n")
    # A continuation-only packet: follow its skip count to this stream's next.
    edit(xc,
         "      return (next_packet_index * kBitsPerPacket) + packet_frame_offset;\n"
         "    }\n"
         "    next_packet_index++;\n",
         "      return (next_packet_index * kBitsPerPacket) + packet_frame_offset;\n"
         "    }\n"
         "    const uint8_t next_skip = xma::GetPacketSkipCount(next_packet);  // [mp3]\n"
         "    if (next_skip == 0xFF) {\n      break;\n    }\n"
         "    next_packet_index += next_skip + 1;\n")
    # readback_resolve "fast" (Max Payne 3's HDR adaptation reads the
    # luminance it resolved on the CPU, Xenia game-compatibility #42) hands
    # the game the previous resolve's buffer without checking that the GPU
    # has written it: the slot can still be queued, or be fresh memory never
    # written at all. The game's exposure then adapts to garbage (NaN sticks)
    # and gameplay goes dark. As the memexport readback beside it does:
    # remember the submission that wrote each slot, wait for it when it is in
    # flight, and when it is this very submission (a second resolve to the
    # same place this frame) fall back to a full sync of the current one.
    vcp = sdk / "src" / "graphics" / "vulkan" / "command_processor.cpp"
    # Occlusion queries: when the 8192 host queries wrapped, AcquireOcclusionQueryIndex waited
    # for every pending one (the GPU drained) - every few frames in scenes full of light flares,
    # up to a quarter of the GPU thread's time in the chapter 1 log. Only a pending query on the
    # index being reused has to be waited for: the queue is in allocation order, so that is its
    # front, and it is 8192 queries old (long done) in practice.
    edit(vcp,
         "  if (occlusion_query_cursor_ >= kMaxOcclusionQueries) {\n"
         "    // Results still in flight must not be overwritten by the reused indices.\n"
         "    CompleteOcclusionQueries(true);\n"
         "    occlusion_query_cursor_ = 0;\n"
         "  }\n"
         "  host_index_out = occlusion_query_cursor_++;\n",
         "  if (occlusion_query_cursor_ >= kMaxOcclusionQueries) {\n"
         "    occlusion_query_cursor_ = 0;\n"
         "  }\n"
         "  // [mp3] A result still in flight must not be overwritten by the reused index: only the\n"
         "  // oldest pending query can hold it (allocation order), so wait for that one alone.\n"
         "  while (!pending_occlusion_queries_.empty() &&\n"
         "         pending_occlusion_queries_.front().host_index == occlusion_query_cursor_ && !device_lost_) {\n"
         "    const uint64_t submission = pending_occlusion_queries_.front().submission;\n"
         "    if (submission_completed_ < submission) {\n"
         "      CheckSubmissionFenceAndDeviceLoss(submission);\n"
         "      if (submission_completed_ < submission) break;\n"
         "    }\n"
         "    CompleteOcclusionQueries(false);\n"
         "  }\n"
         "  host_index_out = occlusion_query_cursor_++;\n")
    edit(vcp,
         "  PushBufferMemoryBarrier(readback.buffers[write_index], 0, written_length,\n"
         "                          VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,\n"
         "                          VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT);\n",
         "  PushBufferMemoryBarrier(readback.buffers[write_index], 0, written_length,\n"
         "                          VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,\n"
         "                          VK_ACCESS_TRANSFER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT);\n"
         "  readback.submission_written[write_index] = GetCurrentSubmission();  // [mp3]\n"
         "  readback.written_size[write_index] = written_length;\n")
    edit(vcp,
         "  bool is_cache_miss = false;\n"
         "  if (use_delayed_sync && (readback.buffers[read_index] == VK_NULL_HANDLE ||\n"
         "                           written_length > readback.sizes[read_index] ||\n"
         "                           readback.mapped_data[read_index] == nullptr)) {\n",
         "  bool is_cache_miss = false;\n"
         "  // [mp3] The previous slot is usable once the GPU has written it; until\n"
         "  // then the guest keeps the older data (waiting here held the CPU to\n"
         "  // the GPU's previous frame, every frame).\n"
         "  if (use_delayed_sync && readback.buffers[read_index] != VK_NULL_HANDLE &&\n"
         "      readback.submission_written[read_index] &&\n"
         "      readback.submission_written[read_index] > submission_completed_ &&\n"
         "      written_length <= readback.written_size[read_index]) {\n"
         "    CheckSubmissionFenceAndDeviceLoss(0);\n"
         "    if (readback.submission_written[read_index] > submission_completed_) {\n"
         "      rex::mp3_perf::g_n[rex::mp3_perf::kReadbackLate].fetch_add(1, std::memory_order_relaxed);\n"
         "      // (the same slot takes the next copy too: the older one gets time)\n"
         "      return true;\n"
         "    }\n"
         "  }\n"
         "  if (use_delayed_sync && (readback.buffers[read_index] == VK_NULL_HANDLE ||\n"
         "                           written_length > readback.sizes[read_index] ||\n"
         "                           readback.mapped_data[read_index] == nullptr ||\n"
         "                           written_length > readback.written_size[read_index] ||\n"
         "                           !readback.submission_written[read_index] ||\n"
         "                           readback.submission_written[read_index] > submission_completed_)) {\n")

    # XMA loops: every gameplay decode error (site 2, 22k per 10 s) was the
    # framework's own "invalid loop interval" check, which Xenia doesn't
    # have. It rejects legal loop data (loop_subframe_end is a 3-bit field,
    # 4..7 meaning the whole frame) and stops every looping voice (ambience,
    # music, gunfire tails) at its loop end; the error then sticks ("begun
    # in error"). The loop code below copes with any values (the output
    # limit is clamped to the frame). The loop-start skip is applied as
    # Xenia does: only a skip shorter than the frame.
    edit(xc,
         "  if (is_loop_end_frame &&\n"
         "      (data->loop_subframe_end > 3 || data->loop_subframe_skip > 4 ||\n",
         "  if (false && is_loop_end_frame &&  // [mp3] as Xenia: no such check\n"
         "      (data->loop_subframe_end > 3 || data->loop_subframe_skip > 4 ||\n")
    edit(xc,
         "      current_frame_remaining_subframes_ -=\n"
         "          std::min<uint8_t>(skip, current_frame_remaining_subframes_);\n",
         "      if (skip < current_frame_remaining_subframes_) {  // [mp3] as Xenia\n"
         "        current_frame_remaining_subframes_ -= skip;\n      }\n")

    # Frame capture (with a logs/capture.txt file): at 90, 150, 210 and 300 s
    # after the first draw, one whole frame goes to logs/mp3_frame_<s>.bin:
    # every draw (render target, depth, blend registers, shaders, the pixel
    # shader's float constants, the texture fetch constants it samples),
    # every resolve (copy registers and the resolved bytes as the CPU sees
    # them) in order, and each shader's ucode once. tools/mp3_frame.py turns
    # it into images of every pass.
    edit(vcp, "#include <cstring>\n#include <iterator>\n",
         "#include <chrono>\n#include <cstdio>\n#include <cstring>\n#include <iterator>\n"
         "#include <unordered_set>\n")
    edit(vcp,
         "namespace rex::graphics::vulkan {\n\nnamespace {\n",
         "namespace rex::graphics::vulkan {\n\nnamespace {\n\n"
         "// [mp3] Frame capture (see ci/patch_framework.py).\n"
         "struct Mp3Capture {\n"
         "  std::FILE* file = nullptr;\n"
         "  uint64_t frame = 0, armed_frame = 0;\n"
         "  uint32_t taken = 0, seq = 0;\n"
         "  std::chrono::steady_clock::time_point start{};\n"
         "  std::unordered_set<uint64_t> shaders;\n"
         "  std::unordered_set<uint64_t> buffers;  // (each vertex buffer's bytes once a capture)\n"
         "};\n"
         "Mp3Capture mp3_cap;\n"
         "constexpr uint32_t kMp3CaptureSeconds[] = {120, 121, 180, 181, 240, 241};\n"
         "void Mp3CapWrite(const void* p, size_t n) {\n"
         "  if (mp3_cap.file) std::fwrite(p, 1, n, mp3_cap.file);\n"
         "}\n"
         "template <typename T>\n"
         "void Mp3CapPut(const T& v) {\n  Mp3CapWrite(&v, sizeof(v));\n}\n"
         "bool Mp3CapActive(uint64_t frame) {\n"
         "  // (only with logs/capture.txt: a capture holds its frame up). Asked first: this runs for\n"
         "  // every draw, and the clock read below was a tenth of the GPU thread's time\n"
         "  static const bool enabled = [] {\n"
         "    std::FILE* f = std::fopen(\"/app0/logs/capture.txt\", \"rb\");\n"
         "    if (f) std::fclose(f);\n"
         "    return f != nullptr;\n"
         "  }();\n"
         "  if (!enabled) return false;\n"
         "  const auto now = std::chrono::steady_clock::now();\n"
         "  if (mp3_cap.start == std::chrono::steady_clock::time_point{}) mp3_cap.start = now;\n"
         "  if (mp3_cap.file && frame != mp3_cap.frame) {\n"
         "    std::fclose(mp3_cap.file);\n    mp3_cap.file = nullptr;\n"
         "    REXGPU_INFO(\"mp3 capture: frame {} written ({} records)\", mp3_cap.frame, mp3_cap.seq);\n"
         "  }\n"
         "  if (!mp3_cap.file && enabled && mp3_cap.taken < std::size(kMp3CaptureSeconds)) {\n"
         "    const auto seconds =\n"
         "        std::chrono::duration_cast<std::chrono::seconds>(now - mp3_cap.start).count();\n"
         "    if (!mp3_cap.armed_frame && seconds >= kMp3CaptureSeconds[mp3_cap.taken]) {\n"
         "      mp3_cap.armed_frame = frame + 1;\n"
         "    }\n"
         "    if (mp3_cap.armed_frame && frame >= mp3_cap.armed_frame) {\n"
         "      char path[64];\n"
         "      std::snprintf(path, sizeof(path), \"/app0/logs/mp3_frame_%u.bin\",\n"
         "                    unsigned(kMp3CaptureSeconds[mp3_cap.taken]));\n"
         "      mp3_cap.file = std::fopen(path, \"wb\");\n"
         "      mp3_cap.frame = frame;\n      mp3_cap.armed_frame = 0;\n      mp3_cap.seq = 0;\n"
         "      ++mp3_cap.taken;\n      mp3_cap.shaders.clear();\n      mp3_cap.buffers.clear();\n"
         "      if (mp3_cap.file) {\n"
         "        Mp3CapWrite(\"MP3F\", 4);\n        Mp3CapPut(uint32_t(3));\n        Mp3CapPut(frame);\n"
         "      }\n"
         "    }\n"
         "  }\n"
         "  return mp3_cap.file != nullptr;\n"
         "}\n"
         "void Mp3CapShader(const Shader* shader) {\n"
         "  if (!shader || !mp3_cap.shaders.insert(shader->ucode_data_hash()).second) return;\n"
         "  Mp3CapWrite(\"S\", 1);\n"
         "  Mp3CapPut(shader->ucode_data_hash());\n"
         "  Mp3CapPut(uint32_t(shader->type()));\n"
         "  Mp3CapPut(uint32_t(shader->ucode_dword_count()));\n"
         "  Mp3CapWrite(shader->ucode_dwords(), shader->ucode_dword_count() * 4);\n"
         "}\n"
         "void Mp3CapRegs(const RegisterFile& regs) {\n"
         "  static const uint32_t kRegs[] = {\n"
         "      XE_GPU_REG_RB_SURFACE_INFO,  XE_GPU_REG_RB_COLOR_INFO,     XE_GPU_REG_RB_COLOR1_INFO,\n"
         "      XE_GPU_REG_RB_COLOR2_INFO,   XE_GPU_REG_RB_COLOR3_INFO,    XE_GPU_REG_RB_DEPTH_INFO,\n"
         "      XE_GPU_REG_RB_COLOR_MASK,    XE_GPU_REG_RB_DEPTHCONTROL,   XE_GPU_REG_RB_MODECONTROL,\n"
         "      XE_GPU_REG_RB_BLENDCONTROL0, XE_GPU_REG_RB_COPY_CONTROL,   XE_GPU_REG_RB_COPY_DEST_INFO,\n"
         "      XE_GPU_REG_RB_COPY_DEST_PITCH, XE_GPU_REG_RB_COPY_DEST_BASE};\n"
         "  for (uint32_t r : kRegs) Mp3CapPut(regs[r]);\n"
         "}\n")
    edit(vcp,
         "  bool memexport_writes_possible = memexport_used_vertex || memexport_used_pixel;\n",
         "  bool memexport_writes_possible = memexport_used_vertex || memexport_used_pixel;\n"
         "  if (Mp3CapActive(frame_current_)) {  // [mp3] frame capture: the draw\n"
         "    Mp3CapShader(vertex_shader);\n    Mp3CapShader(pixel_shader);\n"
         "    Mp3CapWrite(\"D\", 1);\n    Mp3CapPut(mp3_cap.seq++);\n"
         "    Mp3CapRegs(regs);\n"
         "    Mp3CapPut(uint32_t(prim_type));\n    Mp3CapPut(index_count);\n"
         "    Mp3CapPut(vertex_shader->ucode_data_hash());\n"
         "    Mp3CapPut(pixel_shader ? pixel_shader->ucode_data_hash() : uint64_t(0));\n"
         "    Mp3CapWrite(&regs.values[XE_GPU_REG_SHADER_CONSTANT_000_X + 256 * 4], 256 * 16);\n"
         "    const auto* bindings = pixel_shader ? &pixel_shader->texture_bindings() : nullptr;\n"
         "    Mp3CapPut(uint32_t(bindings ? bindings->size() : 0));\n"
         "    if (bindings) {\n"
         "      for (const auto& binding : *bindings) {\n"
         "        Mp3CapPut(binding.fetch_constant);\n"
         "        Mp3CapWrite(&regs.values[XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0 + 6 * binding.fetch_constant],\n"
         "                    6 * 4);\n"
         "      }\n"
         "    }\n"
         "    // (version 2) the vertex shader's: its float and bool constants, its vertex fetch\n"
         "    // constants and their buffers' bytes as the CPU sees them\n"
         "    Mp3CapWrite(&regs.values[XE_GPU_REG_SHADER_CONSTANT_000_X], 256 * 16);\n"
         "    Mp3CapWrite(&regs.values[XE_GPU_REG_SHADER_CONSTANT_BOOL_000_031], 8 * 4);\n"
         "    const auto& vbindings = vertex_shader->vertex_bindings();\n"
         "    Mp3CapPut(uint32_t(vbindings.size()));\n"
         "    for (const auto& vb : vbindings) {\n"
         "      const xenos::xe_gpu_vertex_fetch_t vf = regs.GetVertexFetch(vb.fetch_constant);\n"
         "      Mp3CapPut(vb.fetch_constant);\n      Mp3CapPut(vb.stride_words);\n"
         "      Mp3CapPut(vf.dword_0);\n      Mp3CapPut(vf.dword_1);\n"
         "      const uint32_t vaddr = vf.address << 2;\n"
         "      const uint32_t vsize = std::min(uint32_t(vf.size) << 2, uint32_t(8) << 20);\n"
         "      const bool first = mp3_cap.buffers.insert((uint64_t(vaddr) << 32) | vsize).second;\n"
         "      const uint8_t* vdata =\n"
         "          first && vaddr < 0x20000000u ? memory_->TranslatePhysical(vaddr) : nullptr;\n"
         "      Mp3CapPut(uint32_t(vdata ? vsize : 0));\n"
         "      if (vdata) Mp3CapWrite(vdata, vsize);\n"
         "    }\n"
         "    // (version 3) the index buffer: format, endianness, count, address, its bytes once\n"
         "    const uint32_t ibase = index_buffer_info ? index_buffer_info->guest_base : 0;\n"
         "    const uint32_t ilen = index_buffer_info ? uint32_t(std::min(index_buffer_info->length,\n"
         "                                                                size_t(4) << 20)) : 0;\n"
         "    Mp3CapPut(uint32_t(index_buffer_info ? uint32_t(index_buffer_info->format) : 0xFFFFFFFFu));\n"
         "    Mp3CapPut(uint32_t(index_buffer_info ? uint32_t(index_buffer_info->endianness) : 0));\n"
         "    Mp3CapPut(index_buffer_info ? index_buffer_info->count : 0u);\n"
         "    Mp3CapPut(ibase);\n    Mp3CapPut(ilen);\n"
         "    const bool ifirst = ilen && mp3_cap.buffers.insert((uint64_t(ibase) << 32) | ilen | 1).second;\n"
         "    const uint8_t* idata = ifirst && ibase < 0x20000000u ? memory_->TranslatePhysical(ibase) : nullptr;\n"
         "    Mp3CapPut(uint32_t(idata ? ilen : 0));\n"
         "    if (idata) Mp3CapWrite(idata, ilen);\n"
         "  }\n")
    edit(vcp,
         "    resolve_succeeded = IssueCopy_ReadbackResolvePath();\n  }\n",
         "    resolve_succeeded = IssueCopy_ReadbackResolvePath();\n  }\n"
         "  if (Mp3CapActive(frame_current_)) {  // [mp3] frame capture: the resolve\n"
         "    Mp3CapWrite(\"C\", 1);\n    Mp3CapPut(mp3_cap.seq++);\n"
         "    Mp3CapRegs(*register_file_);\n"
         "    Mp3CapPut(uint32_t(resolve_succeeded));\n"
         "    Mp3CapPut(frame_flow_resolve_address_);\n"
         "    const uint8_t* resolved = frame_flow_resolve_length_ <= (16u << 20)\n"
         "        ? memory_->TranslatePhysical(frame_flow_resolve_address_) : nullptr;\n"
         "    Mp3CapPut(uint32_t(resolved ? frame_flow_resolve_length_ : 0));\n"
         "    if (resolved) Mp3CapWrite(resolved, frame_flow_resolve_length_);\n"
         "  }\n")
    # (version 3) After each captured draw, the GPU's own copy (the shared memory buffer) of its
    # vertex and index buffers, each once a capture ("G" records), to compare with what the CPU
    # wrote: a stale or wrong GPU copy shows up as a difference.
    edit(vcp,
         "        IssueDraw_MemexportReadbackFullPath(memexport_total_size);\n"
         "      }\n"
         "    }\n"
         "  }\n"
         "\n"
         "  return true;\n"
         "}\n",
         "        IssueDraw_MemexportReadbackFullPath(memexport_total_size);\n"
         "      }\n"
         "    }\n"
         "  }\n"
         "\n"
         "  if (Mp3CapActive(frame_current_)) {  // [mp3] frame capture: the GPU's copy of the buffers\n"
         "    std::vector<std::pair<uint32_t, uint32_t>> mp3_ranges;\n"
         "    for (const auto& vb : vertex_shader->vertex_bindings()) {\n"
         "      const xenos::xe_gpu_vertex_fetch_t vf = regs.GetVertexFetch(vb.fetch_constant);\n"
         "      mp3_ranges.emplace_back(vf.address << 2, std::min(uint32_t(vf.size) << 2, uint32_t(8) << 20));\n"
         "    }\n"
         "    if (index_buffer_info && index_buffer_info->guest_base) {\n"
         "      mp3_ranges.emplace_back(index_buffer_info->guest_base,\n"
         "                              uint32_t(std::min(index_buffer_info->length, size_t(4) << 20)));\n"
         "    }\n"
         "    static std::unordered_set<uint64_t> mp3_gpu_seen;\n"
         "    static uint64_t mp3_gpu_frame = UINT64_MAX;\n"
         "    if (mp3_gpu_frame != frame_current_) {\n"
         "      mp3_gpu_seen.clear();\n      mp3_gpu_frame = frame_current_;\n"
         "    }\n"
         "    const ui::vulkan::VulkanDevice* const mp3_device = GetVulkanDevice();\n"
         "    const ui::vulkan::VulkanDevice::Functions& mp3_dfn = mp3_device->functions();\n"
         "    for (const auto& [mp3_addr, mp3_size] : mp3_ranges) {\n"
         "      if (!mp3_size || mp3_addr >= SharedMemory::kBufferSize ||\n"
         "          mp3_size > SharedMemory::kBufferSize - mp3_addr ||\n"
         "          !mp3_gpu_seen.insert((uint64_t(mp3_addr) << 32) | mp3_size).second) {\n"
         "        continue;\n"
         "      }\n"
         "      VkBuffer mp3_buffer = VK_NULL_HANDLE;\n"
         "      VkDeviceMemory mp3_memory = VK_NULL_HANDLE;\n"
         "      uint32_t mp3_memory_type = UINT32_MAX;\n"
         "      VkDeviceSize mp3_memory_size = 0;\n"
         "      if (!ui::vulkan::util::CreateDedicatedAllocationBuffer(\n"
         "              mp3_device, mp3_size, VK_BUFFER_USAGE_TRANSFER_DST_BIT,\n"
         "              ui::vulkan::util::MemoryPurpose::kReadback, mp3_buffer, mp3_memory,\n"
         "              &mp3_memory_type, &mp3_memory_size)) {\n"
         "        continue;\n"
         "      }\n"
         "      shared_memory_->Use(VulkanSharedMemory::Usage::kRead);\n"
         "      SubmitBarriers(true);\n"
         "      VkBufferCopy mp3_region = {};\n"
         "      mp3_region.srcOffset = mp3_addr;\n"
         "      mp3_region.size = mp3_size;\n"
         "      deferred_command_buffer_.CmdVkCopyBuffer(shared_memory_->buffer(), mp3_buffer, 1, &mp3_region);\n"
         "      PushBufferMemoryBarrier(mp3_buffer, 0, mp3_size, VK_PIPELINE_STAGE_TRANSFER_BIT,\n"
         "                              VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_TRANSFER_WRITE_BIT,\n"
         "                              VK_ACCESS_HOST_READ_BIT);\n"
         "      void* mp3_mapping = nullptr;\n"
         "      if (AwaitAllQueueOperationsCompletion() &&\n"
         "          mp3_dfn.vkMapMemory(mp3_device->device(), mp3_memory, 0, VK_WHOLE_SIZE, 0, &mp3_mapping) ==\n"
         "              VK_SUCCESS) {\n"
         "        if (!(mp3_device->memory_types().host_coherent & (uint32_t(1) << mp3_memory_type))) {\n"
         "          VkMappedMemoryRange mp3_range = {};\n"
         "          mp3_range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;\n"
         "          mp3_range.memory = mp3_memory;\n"
         "          mp3_range.size = VK_WHOLE_SIZE;\n"
         "          mp3_dfn.vkInvalidateMappedMemoryRanges(mp3_device->device(), 1, &mp3_range);\n"
         "        }\n"
         "        Mp3CapWrite(\"G\", 1);\n        Mp3CapPut(mp3_addr);\n        Mp3CapPut(mp3_size);\n"
         "        Mp3CapWrite(mp3_mapping, mp3_size);\n"
         "        mp3_dfn.vkUnmapMemory(mp3_device->device(), mp3_memory);\n"
         "      }\n"
         "      mp3_dfn.vkDestroyBuffer(mp3_device->device(), mp3_buffer, nullptr);\n"
         "      mp3_dfn.vkFreeMemory(mp3_device->device(), mp3_memory, nullptr);\n"
         "    }\n"
         "  }\n"
         "\n"
         "  return true;\n"
         "}\n")

    # A draw whose depth test can never pass and that has stencil off writes
    # nothing (no color, depth or stencil): skip it before it binds render
    # targets. Max Payne 3 issues one (depth only, zfunc NEVER) on its depth
    # buffer reinterpreted as 640-wide 2x MSAA between the G-buffer and the
    # stencil-tested deferred lighting; on host render targets binding it
    # converts the depth/stencil away and back (lossy) for nothing.
    edit(vcp,
         "  if (surface_pitch_is_zero && is_rasterization_done) {\n",
         "  if (is_rasterization_done && !memexport_used_vertex) {  // [mp3]\n"
         "    const auto depth_control = draw_util::GetNormalizedDepthControl(regs);\n"
         "    if (depth_control.z_enable && !depth_control.stencil_enable &&\n"
         "        depth_control.zfunc == xenos::CompareFunction::kNever) {\n"
         "      ++frame_flow_no_effect_draws_;\n      return true;\n    }\n"
         "  }\n"
         "  if (surface_pitch_is_zero && is_rasterization_done) {\n")
    vrt = sdk / "src" / "graphics" / "vulkan" / "render_target_cache.cpp"
    edit(vrt,
         "  if (GetPath() == Path::kPixelShaderInterlock) {\n"
         "    // The first operation will likely be drawing.\n",
         "  REXGPU_INFO(\"VulkanRenderTargetCache: {} path (fragment shader interlock {})\",  // [mp3]\n"
         "              path_ == Path::kPixelShaderInterlock ? \"fragment shader interlock (EDRAM)\"\n"
         "                                                   : \"host render targets\",\n"
         "              fsi_path_supported ? \"supported\" : \"unsupported\");\n"
         "  if (GetPath() == Path::kPixelShaderInterlock) {\n"
         "    // The first operation will likely be drawing.\n")

    # Integer number format (Xenia canary packs and fetches it, this
    # framework's resolve shaders and texture fetch only know fractions).
    # Max Payne 3 resolves its HDR scene (7e3) to k_16_16_16_16 with number
    # format "unsigned integer" and exponent bias +11 (a 16-bit integer with 11
    # fractional bits) and samples it with num_format integer and exp_adjust
    # -13. Packed as a 0..1 fraction every lit pixel saturated (all white);
    # fetched as a fraction it would read 65535 times too dark.
    # - resolve: for 16-bit components, 2^(bias - 16) through the fraction
    #   packer writes the same integers as 2^bias through an integer packer
    #   (to 1/65536).
    # - fetch: an integer-format 16-bit texture gets exp_adjust + 16 in the
    #   fetch constants the shaders read, the fraction times 65535 (to 1/65536).
    drw = sdk / "src" / "graphics" / "util" / "draw.cpp"
    edit(drw,
         "  // Handle k_16_16 and k_16_16_16_16 range.\n"
         "  info_out.copy_dest_info.copy_dest_exp_bias = exp_bias;\n",
         "  // [mp3] Unsigned integer destinations through the fraction packer.\n"
         "  if (!is_depth && rb_copy_dest_info.copy_dest_number ==\n"
         "                       xenos::SurfaceNumberFormat::kUnsignedInteger) {\n"
         "    switch (xenos::ColorFormat(dest_format)) {\n"
         "      case xenos::ColorFormat::k_16:\n"
         "      case xenos::ColorFormat::k_16_16:\n"
         "      case xenos::ColorFormat::k_16_16_16_16:\n"
         "        exp_bias = std::max(exp_bias - int32_t(16), int32_t(-32));\n"
         "        break;\n"
         "      default:\n"
         "        break;\n"
         "    }\n"
         "  }\n"
         "  // Handle k_16_16 and k_16_16_16_16 range.\n"
         "  info_out.copy_dest_info.copy_dest_exp_bias = exp_bias;\n")
    edit(vcp,
         "      std::memcpy(mapping, &regs[XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0], kFetchConstantsSize);\n",
         "      std::memcpy(mapping, &regs[XE_GPU_REG_SHADER_CONSTANT_FETCH_00_0], kFetchConstantsSize);\n"
         "      // [mp3] Integer-format 16-bit textures: fraction * 65535 (see\n"
         "      // ci/patch_framework.py).\n"
         "      for (uint32_t i = 0; i < 32; ++i) {\n"
         "        uint32_t* fetch = reinterpret_cast<uint32_t*>(mapping) + 6 * i;\n"
         "        const uint32_t type = fetch[0] & 3, format = fetch[1] & 0x3F;\n"
         "        if ((type != 2 && type != 0) || !(fetch[3] & 1) ||\n"
         "            (format != 24 && format != 25 && format != 26)) {\n"
         "          continue;\n"
         "        }\n"
         "        const int32_t exp_adjust =\n"
         "            std::min((int32_t(fetch[3] << 13) >> 26) + 16, int32_t(31));\n"
         "        fetch[3] = (fetch[3] & ~(UINT32_C(0x3F) << 13)) | ((uint32_t(exp_adjust) & 0x3F) << 13);\n"
         "      }\n")

    # Readback only what the CPU reads: "fast" copied every resolve of every
    # frame back (55 a frame, ~40 MB with the 7.5 MB HDR copies) and memcpy'd
    # it into guest memory on the GPU thread. The game's CPU reads are its
    # luminance / exposure resolves (a few KB); the GPU never needs these
    # copies. Up to 256 KB, as Xenia's purpose for it (HDR adaptation).
    edit(vcp,
         "  if (!memory_->TranslatePhysical(written_address)) {\n    return true;\n  }\n",
         "  if (!memory_->TranslatePhysical(written_address)) {\n    return true;\n  }\n"
         "  if (written_length > (256u << 10) && !mp3_cap.file) {  // [mp3] only what the CPU reads\n"
         "    return true;\n  }\n")
    edit(vcp,
         "  uint32_t read_index = write_index;\n  if (use_delayed_sync) {\n",
         "  if (mp3_cap.file) use_delayed_sync = false;  // [mp3] captured frames: current data\n"
         "  uint32_t read_index = write_index;\n  if (use_delayed_sync) {\n")

    # Texture fetch result exponent bias (exp_adjust) from the fetch constant
    # word 3, bits 13:18 (xenos.h, and the DXBC translator). The SPIR-V
    # translator took bits 13:18 of word 4 - part of the LOD bias - so on
    # Vulkan no texture got its exp_adjust, and textures with a LOD bias got a
    # random scale. Max Payne 3 reads its HDR scene, bloom and luminance with
    # exp_adjust -13 (+16 above for the integer format): every post-processing
    # pass saw the scene 8 times too dark (the dark, grey gameplay).
    # Alpha to coverage on the EDRAM path (fragment shader interlock) did nothing on the PS5. The
    # GPU supports demote-to-helper, and on that path the coverage it computed (in its own branch,
    # never merged out of it) wasn't merged into the pixel's sample mask, only on the other path:
    # every alpha-to-coverage draw came out fully covered. The grass around Max fades out by
    # alpha to coverage (its cards' alpha down to a quarter within a metre of him): drawn solid,
    # the cards turned into big grass-textured triangles across the screen, worst near the camera
    # (Chapter V; reported on 1.0.3, there since the EDRAM path). As Xenia canary does it now:
    # the coverage merged out of its branch, and into the mask on both paths.
    srb = sdk / "src" / "graphics" / "pipeline" / "shader" / "spirv_translator_rb.cpp"
    edit(srb,
         "      SpirvBuilder::IfBuilder if_alpha_to_coverage_enabled(\n"
         "          builder_->createBinOp(spv::OpINotEqual, type_bool_, alpha_to_mask, const_uint_0_),\n"
         "          spv::SelectionControlDontFlattenMask, *builder_);\n",
         "      // [mp3] the mask without alpha to coverage, for the merge after its branch\n"
         "      const spv::Id fsi_sample_mask_before_alpha_to_coverage = fsi_sample_mask_in_rt_0_alpha_tests;\n"
         "      SpirvBuilder::IfBuilder if_alpha_to_coverage_enabled(\n"
         "          builder_->createBinOp(spv::OpINotEqual, type_bool_, alpha_to_mask, const_uint_0_),\n"
         "          spv::SelectionControlDontFlattenMask, *builder_);\n")
    edit(srb,
         "      if_alpha_to_coverage_enabled.makeEndIf();\n"
         "    }\n",
         "      if_alpha_to_coverage_enabled.makeEndIf();\n"
         "      if (edram_fragment_shader_interlock_) {\n"
         "        // [mp3] out of the branch it was computed in\n"
         "        fsi_sample_mask_in_rt_0_alpha_tests = if_alpha_to_coverage_enabled.createMergePhi(\n"
         "            fsi_sample_mask_in_rt_0_alpha_tests, fsi_sample_mask_before_alpha_to_coverage);\n"
         "      }\n"
         "    }\n")
    edit(srb,
         "      if (!features_.demote_to_helper_invocation) {\n"
         "        // The tests might have modified the sample mask via\n"
         "        // fsi_sample_mask_in_rt_0_alpha_tests.\n",
         "      {  // [mp3] with demote too: alpha to coverage changes the mask either way\n"
         "        // The tests might have modified the sample mask via\n"
         "        // fsi_sample_mask_in_rt_0_alpha_tests.\n")
    sft = sdk / "src" / "graphics" / "pipeline" / "shader" / "spirv_translator_fetch.cpp"
    edit(sft,
         "        spv::Id result_exponent_bias = builder_->createBinBuiltinCall(\n"
         "            type_float_, ext_inst_glsl_std_450_, GLSLstd450Ldexp, const_float_1_,\n"
         "            builder_->createTriOp(spv::OpBitFieldSExtract, type_int_, fetch_constant_word_4_signed,\n"
         "                                  builder_->makeUintConstant(13), builder_->makeUintConstant(6)));\n",
         "        // [mp3] Word 3 (see ci/patch_framework.py), not word 4.\n"
         "        id_vector_temp_.clear();\n"
         "        id_vector_temp_.push_back(const_int_0_);\n"
         "        id_vector_temp_.push_back(\n"
         "            builder_->makeIntConstant(int((fetch_constant_word_0_index + 3) >> 2)));\n"
         "        id_vector_temp_.push_back(\n"
         "            builder_->makeIntConstant(int((fetch_constant_word_0_index + 3) & 3)));\n"
         "        spv::Id fetch_constant_word_3_signed = builder_->createUnaryOp(\n"
         "            spv::OpBitcast, type_int_,\n"
         "            builder_->createLoad(builder_->createAccessChain(spv::StorageClassUniform,\n"
         "                                                             uniform_fetch_constants_,\n"
         "                                                             id_vector_temp_),\n"
         "                                 spv::NoPrecision));\n"
         "        spv::Id result_exponent_bias = builder_->createBinBuiltinCall(\n"
         "            type_float_, ext_inst_glsl_std_450_, GLSLstd450Ldexp, const_float_1_,\n"
         "            builder_->createTriOp(spv::OpBitFieldSExtract, type_int_, fetch_constant_word_3_signed,\n"
         "                                  builder_->makeUintConstant(13), builder_->makeUintConstant(6)));\n")

    # Where the GPU thread's time goes (include/rex/mp3_perf.h, from
    # fw-files): timers around the stalls and the big steps, printed per frame
    # every 10 s as "mp3 perf". The framework's "ps5 timing" line only counts
    # the GTA IV renderer's frames (all zeros here).
    def include_perf(path):
        text = path.read_text()
        if "#include <rex/mp3_perf.h>" in text:
            return
        i = text.index("\n#include ") + 1
        path.write_text(text[:i] + "#include <rex/mp3_perf.h>\n" + text[i:])
    cpb = sdk / "src" / "graphics" / "command_processor.cpp"
    vtc = sdk / "src" / "graphics" / "vulkan" / "texture_cache.cpp"
    vpc = sdk / "src" / "graphics" / "vulkan" / "pipeline_cache.cpp"
    for f in (cpb, vcp, vtc):
        include_perf(f)
    edit(vcp, "#include <rex/mp3_perf.h>\n",
         "#include <rex/mp3_perf.h>\n#include <rex/ps5_diag.h>\n#include <rex/frame_stats.h>\n"
         "#if defined(__PROSPERO__)\n#include <pthread.h>\n#endif\n")
    edit(cpb,
         "      PrepareForWait();\n      bool poll_background_work = PollBackgroundWork();\n",
         "      rex::mp3_perf::Scope mp3_perf_idle(rex::mp3_perf::kIdle);\n"
         "      PrepareForWait();\n      bool poll_background_work = PollBackgroundWork();\n")
    edit(cpb,
         "      if (wait >= 0x100) {\n        PrepareForWait();\n",
         "      if (wait >= 0x100) {\n"
         "        rex::mp3_perf::Scope mp3_perf_wait(rex::mp3_perf::kWaitRegMem);\n"
         "        PrepareForWait();\n")
    edit(vcp,
         "    VkResult wait_result =\n        vulkan_device->WaitForFences(",
         "    rex::mp3_perf::Scope mp3_perf_fence(rex::mp3_perf::kFenceWait);\n"
         "    VkResult wait_result =\n        vulkan_device->WaitForFences(")
    edit(vcp,
         "bool VulkanCommandProcessor::EndSubmission(bool is_swap) {\n",
         "bool VulkanCommandProcessor::EndSubmission(bool is_swap) {\n"
         "  rex::mp3_perf::Scope mp3_perf_submit(rex::mp3_perf::kSubmit);\n")
    edit(vcp,
         "                                       bool major_mode_explicit) {\n",
         "                                       bool major_mode_explicit) {\n"
         "  rex::mp3_perf::g_n[rex::mp3_perf::kDraw].fetch_add(1, std::memory_order_relaxed);\n")
    edit(vcp,
         "bool VulkanCommandProcessor::IssueCopy() {\n",
         "bool VulkanCommandProcessor::IssueCopy() {\n"
         "  rex::mp3_perf::Scope mp3_perf_copy(rex::mp3_perf::kCopy);\n")
    edit(vtc,
         "bool VulkanTextureCache::LoadTextureDataFromResidentMemoryImpl(Texture& texture, bool load_base,\n"
         "                                                               bool load_mips) {\n",
         "bool VulkanTextureCache::LoadTextureDataFromResidentMemoryImpl(Texture& texture, bool load_base,\n"
         "                                                               bool load_mips) {\n"
         "  rex::mp3_perf::Scope mp3_perf_texture(rex::mp3_perf::kTextureLoad);\n")
    edit(vcp,
         "bool VulkanCommandProcessor::EndGuestOcclusionQuery(uint32_t sample_count_address) {\n",
         "bool VulkanCommandProcessor::EndGuestOcclusionQuery(uint32_t sample_count_address) {\n"
         "  rex::mp3_perf::Scope mp3_perf_occlusion(rex::mp3_perf::kOcclusion);\n")
    edit(vcp,
         "                                       uint32_t frontbuffer_height) {\n  SCOPE_profile_cpu_f(\"gpu\");\n",
         "                                       uint32_t frontbuffer_height) {\n  SCOPE_profile_cpu_f(\"gpu\");\n"
         "  {\n"
         "    // [mp3] the GPU thread's time per frame, every 10 s (rex/mp3_perf.h)\n"
         "    using namespace rex::mp3_perf;\n"
         "    static uint64_t last_us = 0, frames = 0, prev_ns[kCount], prev_n[kCount];\n"
         "    const uint64_t now = NowUs();\n"
         "    {\n"
         "      // a frame over 100 ms on its own line, with where the GPU thread's time went in it\n"
         "      static uint64_t frame_us = 0, frame_ns[kCount], hitches = 0;\n"
         "      uint64_t ns[kCount];\n"
         "      for (uint32_t c = 0; c < kCount; ++c) ns[c] = g_ns[c].load(std::memory_order_relaxed);\n"
         "      if (frame_us && now - frame_us > 100000 && hitches < 500) {\n"
         "        ++hitches;\n"
         "        auto d = [&](Counter c) { return double(ns[c] - frame_ns[c]) / 1e6; };\n"
         "        const double frame_ms = double(now - frame_us) / 1000.0;\n"
         "        // (submit is mostly inside swap; the rest is the draws)\n"
         "        REXGPU_INFO(\"mp3 hitch: a frame of {:.0f} ms; GPU thread: idle (waiting for the game) {:.0f}, \"\n"
         "                    \"GPU fences {:.0f}, swap {:.0f} (submits {:.0f}), copies {:.0f}, occlusion queries {:.0f}, \"\n"
         "                    \"wait_reg_mem {:.0f}, texture loads {:.0f} (HD {:.0f}), the rest (draws) {:.0f}\",\n"
         "                    frame_ms, d(kIdle), d(kFenceWait), d(kSwap), d(kSubmit), d(kCopy), d(kOcclusion),\n"
         "                    d(kWaitRegMem), d(kTextureLoad), d(kHd),\n"
         "                    std::max(0.0, frame_ms - d(kIdle) - d(kFenceWait) - d(kSwap) - d(kCopy) -\n"
         "                                      d(kOcclusion) - d(kWaitRegMem)));\n"
         "      }\n"
         "      frame_us = now;\n"
         "      std::copy(std::begin(ns), std::end(ns), std::begin(frame_ns));\n"
         "    }\n"
         "    ++frames;\n"
         "    if (!last_us) {\n"
         "      last_us = now;\n"
         "      frames = 0;\n"
         "    } else if (now - last_us >= 10000000) {\n"
         "      double ms[kCount], n[kCount];\n"
         "      for (uint32_t c = 0; c < kCount; ++c) {\n"
         "        const uint64_t ns = g_ns[c].load(std::memory_order_relaxed), cn = g_n[c].load(std::memory_order_relaxed);\n"
         "        ms[c] = double(ns - prev_ns[c]) / 1e6 / double(frames);\n"
         "        n[c] = double(cn - prev_n[c]) / double(frames);\n"
         "        prev_ns[c] = ns;\n"
         "        prev_n[c] = cn;\n"
         "      }\n"
         "      const double frame_ms = double(now - last_us) / 1000.0 / double(frames);\n"
         "      REXGPU_INFO(\"mp3 perf: {:.1f} fps, {:.1f} ms a frame; GPU thread per frame: idle (waiting for the game) {:.1f}, \"\n"
         "                  \"wait_reg_mem {:.1f} ({:.1f}x), GPU fences {:.1f}, submit {:.1f} ({:.1f}x), swap {:.1f}, \"\n"
         "                  \"draws (the rest) {:.1f} ({:.0f}; texture loads {:.1f} ({:.1f}), HD {:.1f} ({:.1f})), \"\n"
         "                  \"copies {:.1f} ({:.1f}), late readbacks {:.2f}, occlusion queries {:.1f} ({:.1f})\",\n"
         "                  1000.0 / frame_ms, frame_ms, ms[kIdle], ms[kWaitRegMem], n[kWaitRegMem], ms[kFenceWait],\n"
         "                  ms[kSubmit], n[kSubmit], ms[kSwap],\n"
         "                  std::max(0.0, frame_ms - ms[kIdle] - ms[kWaitRegMem] - ms[kFenceWait] - ms[kSubmit] -\n"
         "                                    ms[kSwap] - ms[kCopy] - ms[kOcclusion]),\n"
         "                  n[kDraw], ms[kTextureLoad], n[kTextureLoad], ms[kHd], n[kHd], ms[kCopy], n[kCopy],\n"
         "                  n[kReadbackLate], ms[kOcclusion], n[kOcclusion]);\n"
         "      last_us = now;\n"
         "      frames = 0;\n"
         "    }\n"
         "  }\n"
         "#if defined(__PROSPERO__)\n"
         "  {\n"
         "    // [mp3] The GPU thread is the game's limit: a core of its own (CPU 0,\n"
         "    // its twin CPU 1 left empty), the lane on CPU 4, the rest on 2-3 and 6-12 (moved\n"
         "    // when it is new: KeepOtherThreadsOn only touches changed ones).\n"
         "    static uint64_t cpus_us = 0;\n"
         "    const uint64_t now = rex::mp3_perf::NowUs();\n"
         "    if (!cpus_us) {\n"
         "      rex::frame_stats::scePthreadSetaffinity(pthread_self(), 1);\n"
         "      REXGPU_INFO(\"mp3: GPU thread on CPU 0, the lane on 4, the others on CPUs 2-3 and 6-12\");\n"
         "    }\n"
         "    if (!cpus_us || now - cpus_us >= 5000000) {\n"
         "      cpus_us = now;\n"
         "      rex::ps5diag::KeepOtherThreadsOn(0x1FCC, reinterpret_cast<uintptr_t>(pthread_self()), 0, 0);\n"
         "    }\n"
         "  }\n"
         "#endif\n"
         "  rex::frame_stats::NotePresent();  // [mp3] the frame rate counter (mp3_overlay.cpp)\n"
         "  rex::mp3_perf::Scope mp3_perf_swap(rex::mp3_perf::kSwap);\n")
    # [mp3] A vertex buffer bound at the address and size it had last time was never requested from
    # the shared memory again (vertex_buffer_states_ outlived the frame), so the GPU kept its old copy
    # after the game rewrote it: the grass that bends around Max (rebuilt each frame in buffers the
    # game reuses) drew another frame's vertices - triangles stretched across the screen (Chapter V,
    # reported on 1.0.3). Each frame they're requested once again, as in Xenia.
    edit(vcp,
         "    shared_memory_->CheckStalePages(uint32_t(REXCVAR_GET(gpu_stale_check_pages_per_frame)));\n"
         "  }\n"
         "  vertex_buffers_in_sync_[0] = 0;\n"
         "  vertex_buffers_in_sync_[1] = 0;\n",
         "    shared_memory_->CheckStalePages(uint32_t(REXCVAR_GET(gpu_stale_check_pages_per_frame)));\n"
         "  }\n"
         "  InvalidateAllVertexBufferResidency();  // [mp3] (the states too: requested again each frame)\n")

    # Startup: the shader storage's pipelines were all created before the
    # game started (InitializeShaderStorage blocks the launch), one storage
    # that grows every session: 40 s and more of black screen at 5 a second.
    # With async_shader_compilation they go to the creation threads instead
    # (lowest priority) while the game starts; one drawn before it's built gets
    # what a new one gets: the placeholder at once, the real one queued.
    vpc2 = sdk / "src" / "graphics" / "vulkan" / "pipeline_cache.cpp"
    edit(vpc2,
         "  if (!pipeline_creations.empty()) {\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(creation_request_lock_);\n"
         "      startup_loading_ = true;\n",
         "  // [mp3] async: created at first use (see ci/patch_framework.py)\n"
         "  if (!pipeline_creations.empty() && REXCVAR_GET(async_shader_compilation) &&\n"
         "      !creation_threads_.empty()) {\n"
         "    REXGPU_INFO(\"{} stored graphics pipelines queued for the creation threads\", pipeline_creations.size());\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(creation_request_lock_);\n"
         "      for (PipelineCreationArguments& creation : pipeline_creations) {\n"
         "        creation.priority = pipeline_util::kPriorityLowest;\n"
         "        creation_queue_.push(creation);\n"
         "      }\n"
         "    }\n"
         "    creation_request_cond_.notify_all();\n"
         "    pipeline_creations.clear();\n"
         "  }\n"
         "  if (!pipeline_creations.empty()) {\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(creation_request_lock_);\n"
         "      startup_loading_ = true;\n")
    edit(vpc2,
         "    if (found_pipeline == VK_NULL_HANDLE) {\n"
         "      PipelineCreationArguments creation_arguments;\n",
         "    if (found_pipeline == VK_NULL_HANDLE && use_async) {\n"
         "      // [mp3] not built yet (a stored one's first use, or one still being built): its\n"
         "      // draws are skipped until a creation thread has it - the real one queued once, at\n"
         "      // the draw's priority. Nothing is compiled here, on the GPU thread\n"
         "      if (!it->second.is_placeholder.exchange(true, std::memory_order_acq_rel)) {\n"
         "        PipelineCreationArguments creation_arguments_real;\n"
         "        if (TryGetPipelineCreationArgumentsForDescription(description, &*it, creation_arguments_real)) {\n"
         "          creation_arguments_real.priority = async_priority;\n"
         "          {\n"
         "            std::lock_guard<std::mutex> lock(creation_request_lock_);\n"
         "            creation_queue_.push(creation_arguments_real);\n"
         "          }\n"
         "          creation_request_cond_.notify_one();\n"
         "        } else {\n"
         "          it->second.is_placeholder.store(false, std::memory_order_release);\n"
         "        }\n"
         "      }\n"
         "      found_pipeline = it->second.pipeline.load(std::memory_order_acquire);\n"
         "      if (found_pipeline == VK_NULL_HANDLE && it->second.is_placeholder.load(std::memory_order_acquire)) {\n"
         "        pipeline_out = VK_NULL_HANDLE;\n"
         "        pipeline_layout_out = nullptr;\n"
         "        if (pipeline_handle_out) {\n"
         "          *pipeline_handle_out = &it->second;\n"
         "        }\n"
         "        return true;\n"
         "      }\n"
         "    }\n"
         "    if (found_pipeline == VK_NULL_HANDLE) {\n"
         "      PipelineCreationArguments creation_arguments;\n")
    # [mp3] Every submission's end built the whole creation queue on the GPU thread
    # (CreateQueuedPipelinesOnProcessorThread) and then waited for the creation threads'
    # pipelines in flight, so the async path above stalled the frame anyway: with an empty
    # cache (a new install) every new area's burst of pipelines froze the picture. Found in
    # PROTOTYPE: ReEvolution (100-650 ms, all in "submit"). With async compilation the queue
    # is left to the creation threads.
    edit(vpc2,
         "    if (startup_loading) {\n"
         "      creation_request_cond_.notify_one();\n"
         "    } else {\n",
         "    if (startup_loading || REXCVAR_GET(async_shader_compilation)) {  // [mp3]\n"
         "      creation_request_cond_.notify_one();\n"
         "    } else {\n")
    # [mp3] The DualSense light bar in the game's own colour (mp3_game_overlay.cpp: health,
    # hits, painkillers, bullet time) instead of GTA IV's episode colours and police lights.
    sfh = sdk / "include" / "rex" / "input" / "sony_feedback.h"
    sfc = sdk / "src" / "input" / "sony_feedback.cpp"
    edit(sfh,
         "  uint32_t context_identity = 0;\n};\n",
         "  uint32_t context_identity = 0;\n"
         "  bool has_color = false;  // [mp3] the light bar's colour, from the game\n"
         "  std::array<uint8_t, 3> color{};\n};\n")
    edit(sfc,
         "  if (slot.game.wanted > 0 && now_ms >= slot.wanted_started_ms) {\n",
         "  if (slot.game.has_color) {  // [mp3]\n"
         "    output.color = slot.game.color;\n"
         "  } else if (slot.game.wanted > 0 && now_ms >= slot.wanted_started_ms) {\n")
    # [mp3] A shader met for the first time was translated (Xenos microcode to SPIR-V) on the
    # GPU thread in the middle of its draw: about 25 ms each with the EDRAM emulated (612 in
    # 1924 ms on 8 threads at startup), so a new area's ten or so new shaders held one frame
    # 100-300 ms (mp3_015.log: every hitch of a fresh-cache run in "draws", none in submit
    # after the change above). Now worker threads translate them; the draw is skipped until
    # its shaders are in, as a draw waiting for its pipeline is, and the GPU thread does only
    # what it owns (the shader module and the binding layouts) once a translation is done.
    # A shader's translations go to one worker at a time (as at startup).
    vpch = sdk / "include" / "rex" / "graphics" / "vulkan" / "pipeline_cache.h"
    edit(vpch,
         "  bool EnsureShadersTranslated(VulkanShader::VulkanTranslation* vertex_shader,\n"
         "                               VulkanShader::VulkanTranslation* pixel_shader);\n",
         "  bool EnsureShadersTranslated(VulkanShader::VulkanTranslation* vertex_shader,\n"
         "                               VulkanShader::VulkanTranslation* pixel_shader);\n"
         "  // [mp3] true while either is being translated on a worker (queued if new): skip the draw\n"
         "  bool Mp3TranslationPending(VulkanShader::VulkanTranslation* vertex_shader,\n"
         "                             VulkanShader::VulkanTranslation* pixel_shader);\n")
    edit(vpch,
         "  std::unique_ptr<SpirvShaderTranslator> shader_translator_;\n",
         "  std::unique_ptr<SpirvShaderTranslator> shader_translator_;\n"
         "  // [mp3] Shader translation on worker threads (Mp3TranslationPending).\n"
         "  void Mp3TranslationThread(SpirvShaderTranslator* translator);\n"
         "  void Mp3StopTranslationThreads();\n"
         "  std::mutex mp3_translation_lock_;\n"
         "  std::condition_variable mp3_translation_cond_;\n"
         "  std::deque<VulkanShader::VulkanTranslation*> mp3_translation_queue_;\n"
         "  std::vector<VulkanShader::VulkanTranslation*> mp3_translation_done_;\n"
         "  std::set<const void*> mp3_translation_busy_shaders_;\n"
         "  bool mp3_translation_shutdown_ = false;\n"
         "  // (the processor thread's own)\n"
         "  std::set<VulkanShader::VulkanTranslation*> mp3_translation_in_flight_;\n"
         "  std::vector<std::unique_ptr<SpirvShaderTranslator>> mp3_translation_translators_;\n"
         "  std::vector<std::unique_ptr<rex::thread::Thread>> mp3_translation_threads_;\n")
    edit(vpc2,
         "#include <rex/ui/vulkan/util.h>\n"
         "\n"
         "REXCVAR_DEFINE_INT32(\n"
         "    vulkan_pipeline_creation_threads,",
         "#include <rex/ui/vulkan/util.h>\n"
         "\n"
         "REXCVAR_DEFINE_INT32(mp3_shader_translation_threads, 2, \"GPU/Vulkan\",\n"
         "                     \"Threads translating new shaders while their draws wait (0: on the GPU thread, \"\n"
         "                     \"in the draw)\")\n"
         "    .range(0, 8);\n"
         "\n"
         "REXCVAR_DEFINE_INT32(\n"
         "    vulkan_pipeline_creation_threads,")
    edit(vpc2,
         "void VulkanPipelineCache::Shutdown() {\n",
         "void VulkanPipelineCache::Mp3TranslationThread(SpirvShaderTranslator* translator) {\n"
         "  for (;;) {\n"
         "    VulkanShader::VulkanTranslation* job = nullptr;\n"
         "    {\n"
         "      std::unique_lock<std::mutex> lock(mp3_translation_lock_);\n"
         "      for (;;) {\n"
         "        if (mp3_translation_shutdown_) return;\n"
         "        auto it = std::find_if(mp3_translation_queue_.begin(), mp3_translation_queue_.end(),\n"
         "                               [this](VulkanShader::VulkanTranslation* t) {\n"
         "                                 return !mp3_translation_busy_shaders_.count(&t->shader());\n"
         "                               });\n"
         "        if (it != mp3_translation_queue_.end()) {\n"
         "          job = *it;\n"
         "          mp3_translation_queue_.erase(it);\n"
         "          mp3_translation_busy_shaders_.insert(&job->shader());\n"
         "          break;\n"
         "        }\n"
         "        mp3_translation_cond_.wait(lock);\n"
         "      }\n"
         "    }\n"
         "    translator->TranslateAnalyzedShader(*job);\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(mp3_translation_lock_);\n"
         "      mp3_translation_busy_shaders_.erase(&job->shader());\n"
         "      mp3_translation_done_.push_back(job);\n"
         "    }\n"
         "    mp3_translation_cond_.notify_all();\n"
         "  }\n"
         "}\n"
         "\n"
         "void VulkanPipelineCache::Mp3StopTranslationThreads() {\n"
         "  {\n"
         "    std::lock_guard<std::mutex> lock(mp3_translation_lock_);\n"
         "    mp3_translation_shutdown_ = true;\n"
         "  }\n"
         "  mp3_translation_cond_.notify_all();\n"
         "  for (const auto& thread : mp3_translation_threads_) rex::thread::Wait(thread.get(), false);\n"
         "  mp3_translation_threads_.clear();\n"
         "  mp3_translation_translators_.clear();\n"
         "  mp3_translation_queue_.clear();\n"
         "  mp3_translation_done_.clear();\n"
         "  mp3_translation_busy_shaders_.clear();\n"
         "  mp3_translation_in_flight_.clear();\n"
         "}\n"
         "\n"
         "bool VulkanPipelineCache::Mp3TranslationPending(VulkanShader::VulkanTranslation* vertex_shader,\n"
         "                                                VulkanShader::VulkanTranslation* pixel_shader) {\n"
         "  const int32_t threads = REXCVAR_GET(mp3_shader_translation_threads);\n"
         "  if (threads <= 0 || mp3_translation_shutdown_) return false;\n"
         "  if (mp3_translation_threads_.empty()) {\n"
         "    const ui::vulkan::VulkanDevice* const device = command_processor_.GetVulkanDevice();\n"
         "    const bool fsi = render_target_cache_.GetPath() == RenderTargetCache::Path::kPixelShaderInterlock;\n"
         "    for (int32_t i = 0; i < threads; ++i) {\n"
         "      mp3_translation_translators_.push_back(std::make_unique<SpirvShaderTranslator>(\n"
         "          SpirvShaderTranslator::Features(device), render_target_cache_.msaa_2x_attachments_supported(),\n"
         "          render_target_cache_.msaa_2x_no_attachments_supported(), fsi,\n"
         "          render_target_cache_.draw_resolution_scale_x(), render_target_cache_.draw_resolution_scale_y()));\n"
         "      SpirvShaderTranslator* translator = mp3_translation_translators_.back().get();\n"
         "      mp3_translation_threads_.push_back(\n"
         "          rex::thread::Thread::Create({}, [this, translator]() { Mp3TranslationThread(translator); }));\n"
         "      mp3_translation_threads_.back()->set_name(\"Vulkan Shader Translation\");\n"
         "    }\n"
         "    REXGPU_INFO(\"mp3: new shaders translated on {} threads while their draws wait\", threads);\n"
         "  }\n"
         "  if (!mp3_translation_in_flight_.empty()) {\n"
         "    std::vector<VulkanShader::VulkanTranslation*> done;\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(mp3_translation_lock_);\n"
         "      done.swap(mp3_translation_done_);\n"
         "    }\n"
         "    for (VulkanShader::VulkanTranslation* translation : done) {\n"
         "      mp3_translation_in_flight_.erase(translation);\n"
         "      // (translated already: its module and binding layouts, here on the processor thread)\n"
         "      TranslateAnalyzedShader(*shader_translator_, *translation);\n"
         "    }\n"
         "  }\n"
         "  bool pending = false;\n"
         "  for (VulkanShader::VulkanTranslation* translation : {vertex_shader, pixel_shader}) {\n"
         "    if (!translation) continue;\n"
         "    if (mp3_translation_in_flight_.count(translation)) {\n"
         "      pending = true;\n"
         "      continue;\n"
         "    }\n"
         "    if (translation->is_translated()) continue;\n"
         "    if (!translation->shader().is_ucode_analyzed()) {\n"
         "      translation->shader().AnalyzeUcode(ucode_disasm_buffer_);\n"
         "    }\n"
         "    mp3_translation_in_flight_.insert(translation);\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(mp3_translation_lock_);\n"
         "      mp3_translation_queue_.push_back(translation);\n"
         "    }\n"
         "    mp3_translation_cond_.notify_one();\n"
         "    pending = true;\n"
         "  }\n"
         "  return pending;\n"
         "}\n"
         "\n"
         "void VulkanPipelineCache::Shutdown() {\n"
         "  Mp3StopTranslationThreads();  // [mp3]\n")
    edit(vcp,
         "    if (!pipeline_cache_->EnsureShadersTranslated(vertex_shader_translation,\n"
         "                                                  pixel_shader_translation)) {\n"
         "      return draw_fail(\"shader_translation\");\n"
         "    }\n",
         "    // [mp3] a new shader is translated on a worker: the draw waits, as for its pipeline\n"
         "    if (pipeline_cache_->Mp3TranslationPending(vertex_shader_translation, pixel_shader_translation)) {\n"
         "      frame_used_async_placeholder_pipeline_ = true;\n"
         "      ++frame_flow_draw_placeholders_;\n"
         "      return true;\n"
         "    }\n"
         "    if (!pipeline_cache_->EnsureShadersTranslated(vertex_shader_translation,\n"
         "                                                  pixel_shader_translation)) {\n"
         "      return draw_fail(\"shader_translation\");\n"
         "    }\n")
    # [mp3] New pipelines the same way: queued for the creation threads, the draws skipped until
    # one is built - depth-only ones too (no pixel shader: shadows, the depth pass), which were
    # built on the GPU thread, as was the placeholder every other one got first. 529 of the 1014
    # pipelines of the first chapter 1 after a fresh install were built there (mp3_002.log),
    # 184 in the minute from the cutscene to gameplay: the freezes.
    edit(vpc2,
         "  bool use_async = REXCVAR_GET(async_shader_compilation) && !creation_threads_.empty() &&\n"
         "                   pixel_shader && placeholder_pixel_shader_ != VK_NULL_HANDLE;\n",
         "  bool use_async = REXCVAR_GET(async_shader_compilation) && !creation_threads_.empty();\n")
    edit(vpc2,
         "  bool queued_async_creation = false;\n"
         "  if (use_async) {\n"
         "    creation_arguments_real.priority = async_priority;\n"
         "    PipelineCreationArguments creation_arguments_placeholder;\n"
         "    if (TryGetPipelineCreationArgumentsForDescription(description, &pipeline,\n"
         "                                                      creation_arguments_placeholder, true)) {\n"
         "      pipeline.second.is_placeholder.store(true, std::memory_order_release);\n"
         "      if (EnsurePipelineCreated(creation_arguments_placeholder, placeholder_pixel_shader_)) {\n"
         "        {\n"
         "          std::lock_guard<std::mutex> lock(creation_request_lock_);\n"
         "          creation_queue_.push(creation_arguments_real);\n"
         "        }\n"
         "        creation_request_cond_.notify_one();\n"
         "        queued_async_creation = true;\n"
         "      } else {\n"
         "        pipeline.second.is_placeholder.store(false, std::memory_order_release);\n"
         "      }\n"
         "    }\n"
         "  }\n",
         "  bool queued_async_creation = false;\n"
         "  if (use_async) {\n"
         "    // [mp3] queued, no placeholder built: the draws are skipped until it's ready\n"
         "    creation_arguments_real.priority = async_priority;\n"
         "    pipeline.second.is_placeholder.store(true, std::memory_order_release);\n"
         "    {\n"
         "      std::lock_guard<std::mutex> lock(creation_request_lock_);\n"
         "      creation_queue_.push(creation_arguments_real);\n"
         "    }\n"
         "    creation_request_cond_.notify_one();\n"
         "    queued_async_creation = true;\n"
         "  }\n")
    edit(vpc2,
         "  last_pipeline_ = &pipeline;\n"
         "  pipeline_out = pipeline.second.pipeline.load(std::memory_order_acquire);\n"
         "  pipeline_layout_out = pipeline.second.pipeline_layout.load(std::memory_order_acquire);\n"
         "  if (pipeline_handle_out) {\n"
         "    *pipeline_handle_out = &pipeline.second;\n"
         "  }\n"
         "  return pipeline_out != VK_NULL_HANDLE && pipeline_layout_out != nullptr;\n",
         "  last_pipeline_ = &pipeline;\n"
         "  pipeline_out = pipeline.second.pipeline.load(std::memory_order_acquire);\n"
         "  pipeline_layout_out = pipeline.second.pipeline_layout.load(std::memory_order_acquire);\n"
         "  if (pipeline_handle_out) {\n"
         "    *pipeline_handle_out = &pipeline.second;\n"
         "  }\n"
         "  // [mp3] (one being built: the draw is skipped, not failed)\n"
         "  return (pipeline_out != VK_NULL_HANDLE && pipeline_layout_out != nullptr) ||\n"
         "         (queued_async_creation && pipeline_out == VK_NULL_HANDLE);\n")

    # The driver's compiled pipelines kept across sessions (a VkPipelineCache
    # in <cache>/shaders, as gta4-ps5's own renderer keeps one): without it
    # every session compiled every pipeline again, and the stutters in
    # gameplay were those compiles (mp3_008.log: each drop a burst of
    # "Creating graphics pipeline state"). Saved from the GPU thread's 10 s
    # report when new pipelines were made, written on a thread of its own.
    edit(vpc2,
         "namespace rex::graphics::vulkan {\n",
         "namespace rex::graphics::vulkan {\n\n"
         "// [mp3] the driver's pipeline cache, kept across sessions (ci/patch_framework.py)\n"
         "namespace {\n"
         "VkPipelineCache mp3_vk_cache = VK_NULL_HANDLE;\n"
         "const ui::vulkan::VulkanDevice* mp3_vk_cache_device = nullptr;\n"
         "std::string mp3_vk_cache_path;\n"
         "std::atomic<uint32_t> mp3_vk_cache_new{0};\n"
         "std::atomic<bool> mp3_vk_cache_saving{false};\n"
         "void Mp3OpenPipelineCache(const ui::vulkan::VulkanDevice* device, const std::filesystem::path& path) {\n"
         "  if (mp3_vk_cache != VK_NULL_HANDLE || !device) return;\n"
         "  std::vector<uint8_t> data;\n"
         "  if (std::FILE* f = std::fopen(path.string().c_str(), \"rb\")) {\n"
         "    std::fseek(f, 0, SEEK_END);\n"
         "    const long size = std::ftell(f);\n"
         "    std::fseek(f, 0, SEEK_SET);\n"
         "    if (size > 0 && size < (256l << 20)) {\n"
         "      data.resize(size_t(size));\n"
         "      if (std::fread(data.data(), 1, data.size(), f) != data.size()) data.clear();\n"
         "    }\n"
         "    std::fclose(f);\n"
         "  }\n"
         "  VkPipelineCacheCreateInfo info = {VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO};\n"
         "  info.initialDataSize = data.size();\n"
         "  info.pInitialData = data.empty() ? nullptr : data.data();\n"
         "  const auto& dfn = device->functions();\n"
         "  if (dfn.vkCreatePipelineCache(device->device(), &info, nullptr, &mp3_vk_cache) != VK_SUCCESS) {\n"
         "    info.initialDataSize = 0;\n"
         "    info.pInitialData = nullptr;\n"
         "    if (dfn.vkCreatePipelineCache(device->device(), &info, nullptr, &mp3_vk_cache) != VK_SUCCESS)\n"
         "      mp3_vk_cache = VK_NULL_HANDLE;\n"
         "  }\n"
         "  mp3_vk_cache_device = device;\n"
         "  mp3_vk_cache_path = path.string();\n"
         "  REXGPU_INFO(\"mp3: Vulkan pipeline cache {} ({} KB) at {}\", mp3_vk_cache ? \"open\" : \"unavailable\",\n"
         "              data.size() >> 10, mp3_vk_cache_path);\n"
         "}\n"
         "}  // namespace\n\n"
         "// From the GPU thread's 10 s report: saved when pipelines were made since.\n"
         "void Mp3SavePipelineCache() {\n"
         "  // (at most once a minute, and all of it on a thread of its own: reading the\n"
         "  // 13-18 MB cache on the GPU thread every 10 s was a hitch in itself)\n"
         "  static uint32_t reports = 0;\n"
         "  if (mp3_vk_cache == VK_NULL_HANDLE || !mp3_vk_cache_new.load() || ++reports < 6) return;\n"
         "  if (mp3_vk_cache_saving.exchange(true)) return;\n"
         "  reports = 0;\n"
         "  const uint32_t made = mp3_vk_cache_new.exchange(0);\n"
         "  std::thread([made]() {\n"
         "    const auto& dfn = mp3_vk_cache_device->functions();\n"
         "    size_t size = 0;\n"
         "    std::vector<uint8_t> data;\n"
         "    if (dfn.vkGetPipelineCacheData(mp3_vk_cache_device->device(), mp3_vk_cache, &size, nullptr) == VK_SUCCESS &&\n"
         "        size) {\n"
         "      data.resize(size);\n"
         "      if (dfn.vkGetPipelineCacheData(mp3_vk_cache_device->device(), mp3_vk_cache, &size, data.data()) !=\n"
         "          VK_SUCCESS)\n"
         "        data.clear();\n"
         "      data.resize(std::min(size, data.size()));\n"
         "    }\n"
         "    bool ok = false;\n"
         "    if (!data.empty()) {\n"
         "      const std::string tmp = mp3_vk_cache_path + \".tmp\";\n"
         "      if (std::FILE* f = std::fopen(tmp.c_str(), \"wb\")) {\n"
         "        ok = std::fwrite(data.data(), 1, data.size(), f) == data.size();\n"
         "        ok = std::fclose(f) == 0 && ok;\n"
         "      }\n"
         "      if (ok) ok = std::rename(tmp.c_str(), mp3_vk_cache_path.c_str()) == 0;\n"
         "    }\n"
         "    REXGPU_INFO(\"mp3: Vulkan pipeline cache saved ({} KB, {} new pipelines){}\", data.size() >> 10, made,\n"
         "                ok ? \"\" : \" - FAILED\");\n"
         "    mp3_vk_cache_saving.store(false);\n"
         "  }).detach();\n"
         "}\n")
    edit(vpc2, "#include <algorithm>\n", "#include <algorithm>\n#include <cstdio>\n#include <filesystem>\n#include <thread>\n")
    edit(vpc2,
         "  auto shader_storage_root = cache_root / \"shaders\";\n"
         "  auto shader_storage_shareable_root = shader_storage_root / \"shareable\";\n",
         "  auto shader_storage_root = cache_root / \"shaders\";\n"
         "  auto shader_storage_shareable_root = shader_storage_root / \"shareable\";\n"
         "  {\n"
         "    std::error_code mp3_ec;\n"
         "    std::filesystem::create_directories(shader_storage_root, mp3_ec);\n"
         "    Mp3OpenPipelineCache(command_processor_.GetVulkanDevice(),\n"
         "                         shader_storage_root / fmt::format(\"{:08X}.vk-pipelines.bin\", title_id));\n"
         "  }\n")
    edit(vpc2,
         "  VkResult create_result = dfn.vkCreateGraphicsPipelines(device, VK_NULL_HANDLE, 1,\n"
         "                                                         &pipeline_create_info, nullptr, &pipeline);\n",
         "  VkResult create_result = dfn.vkCreateGraphicsPipelines(device, mp3_vk_cache, 1,\n"
         "                                                         &pipeline_create_info, nullptr, &pipeline);\n"
         "  if (create_result == VK_SUCCESS && mp3_vk_cache != VK_NULL_HANDLE) mp3_vk_cache_new.fetch_add(1);\n")
    edit(vcp,
         "      last_us = now;\n      frames = 0;\n    }\n  }\n",
         "      last_us = now;\n      frames = 0;\n      Mp3SavePipelineCache();  // (pipeline_cache.cpp)\n    }\n  }\n")
    edit(vcp,
         "namespace rex::graphics::vulkan {\n\nnamespace {\n",
         "namespace rex::graphics::vulkan {\n\nvoid Mp3SavePipelineCache();  // [mp3] pipeline_cache.cpp\n\nnamespace {\n")

    # The pipeline description storage (<title>.fsi.vk.xpso) held 229 pipelines
    # in every session since the first ones, though each session made new ones
    # (mp3_001.log, 20:22): so every session translated and built those again
    # mid-game. How many were read, kept and written, for the log; and the
    # files seeked to their end before the writer appends.
    edit(vpc2,
         "      pipeline_stored_descriptions.resize(pipeline_storage_kept_count);\n",
         "      pipeline_stored_descriptions.resize(pipeline_storage_kept_count);\n"
         "      REXGPU_INFO(\"mp3: pipeline storage: {} read, {} kept, {} unsupported, {} corrupted\",\n"
         "                  pipeline_storage_read_count, pipeline_storage_kept_count,\n"
         "                  pipeline_storage_skipped_unsupported_count, pipeline_storage_skipped_corrupted_count);\n")
    edit(vpc2,
         "    rex::filesystem::TruncateStdioFile(shader_storage_file_, shader_storage_valid_bytes);\n",
         "    rex::filesystem::TruncateStdioFile(shader_storage_file_, shader_storage_valid_bytes);\n"
         "    rex::filesystem::Seek(shader_storage_file_, 0, SEEK_END);  // [mp3] (for the writer)\n"
         "    REXGPU_INFO(\"mp3: shader storage: {} KB of shaders\", shader_storage_valid_bytes >> 10);\n")
    edit(vpc2,
         "  shader_storage_cache_root_ = cache_root;\n  shader_storage_title_id_ = title_id;\n",
         "  rex::filesystem::Seek(pipeline_storage_file_, 0, SEEK_END);  // [mp3] (for the writer)\n"
         "  shader_storage_cache_root_ = cache_root;\n  shader_storage_title_id_ = title_id;\n")
    edit(vpc2,
         "    if (write_pipeline) {\n"
         "      assert_not_null(pipeline_storage_file_);\n"
         "      fwrite(&pipeline_description, sizeof(pipeline_description), 1, pipeline_storage_file_);\n"
         "    }\n",
         "    if (write_pipeline) {\n"
         "      assert_not_null(pipeline_storage_file_);\n"
         "      const size_t mp3_written =\n"
         "          fwrite(&pipeline_description, sizeof(pipeline_description), 1, pipeline_storage_file_);\n"
         "      static uint32_t mp3_writes = 0, mp3_failed = 0;\n"
         "      mp3_failed += mp3_written != 1;\n"
         "      if ((++mp3_writes % 50) == 1 || mp3_written != 1)\n"
         "        REXGPU_INFO(\"mp3: pipeline storage: {} written this session ({} failed)\", mp3_writes, mp3_failed);\n"
         "    }\n")

    # The stored shaders were translated one at a time on the GPU thread, before
    # the game's main thread is even resumed (rex_app.cpp: blocking): the wait
    # after PLAY grew with every shader the player had met. Now on several
    # threads, each with a translator of its own and a shader's modifications
    # on one thread, as the D3D12 cache does; the shader modules and binding
    # layouts after, on this thread (TranslateAnalyzedShader skips the
    # translator for a translation that's done).
    edit(vpc2, "#include <array>\n", "#include <array>\n#include <chrono>\n")
    edit(vpc2,
         "  if (!translator.TranslateAnalyzedShader(translation)) {\n",
         "  if (translation.is_translated() ? !translation.is_valid()\n"
         "                                  : !translator.TranslateAnalyzedShader(translation)) {\n")
    edit(vpc2,
         "  // Translate shader modifications needed for stored pipelines.\n"
         "  for (const std::pair<uint64_t, uint64_t>& translation_needed : shader_translations_needed) {\n"
         "    auto shader_it = shaders_.find(translation_needed.first);\n"
         "    if (shader_it == shaders_.end()) {\n"
         "      continue;\n"
         "    }\n"
         "    auto* shader = shader_it->second;\n"
         "    if (!shader->is_ucode_analyzed()) {\n"
         "      shader->AnalyzeUcode(ucode_disasm_buffer_);\n"
         "    }\n"
         "    bool translation_is_new = false;\n"
         "    auto* translation = static_cast<VulkanShader::VulkanTranslation*>(\n"
         "        shader->GetOrCreateTranslation(translation_needed.second, &translation_is_new));\n"
         "    if (!translation->is_translated() &&\n"
         "        !TranslateAnalyzedShader(*shader_translator_, *translation)) {\n"
         "      if (translation_is_new) {\n"
         "        shader->DestroyTranslation(translation_needed.second);\n"
         "      }\n"
         "    }\n"
         "  }\n",
         "  // Translate shader modifications needed for stored pipelines.\n"
         "  // [mp3] on several threads (ci/patch_framework.py)\n"
         "  {\n"
         "    const auto mp3_start = std::chrono::steady_clock::now();\n"
         "    struct Mp3Translation {\n"
         "      VulkanShader* shader;\n"
         "      uint64_t modification;\n"
         "      VulkanShader::VulkanTranslation* translation;\n"
         "      bool is_new;\n"
         "    };\n"
         "    std::vector<Mp3Translation> mp3_jobs;  // (a shader's together: the set's order)\n"
         "    for (const std::pair<uint64_t, uint64_t>& translation_needed : shader_translations_needed) {\n"
         "      auto shader_it = shaders_.find(translation_needed.first);\n"
         "      if (shader_it == shaders_.end()) {\n"
         "        continue;\n"
         "      }\n"
         "      auto* shader = shader_it->second;\n"
         "      if (!shader->is_ucode_analyzed()) {\n"
         "        shader->AnalyzeUcode(ucode_disasm_buffer_);\n"
         "      }\n"
         "      bool translation_is_new = false;\n"
         "      auto* translation = static_cast<VulkanShader::VulkanTranslation*>(\n"
         "          shader->GetOrCreateTranslation(translation_needed.second, &translation_is_new));\n"
         "      if (!translation->is_translated()) {\n"
         "        mp3_jobs.push_back({shader, translation_needed.second, translation, translation_is_new});\n"
         "      }\n"
         "    }\n"
         "    std::vector<size_t> mp3_groups;  // (where each shader's start, and the end)\n"
         "    for (size_t i = 0; i < mp3_jobs.size(); ++i) {\n"
         "      if (!i || mp3_jobs[i].shader != mp3_jobs[i - 1].shader) mp3_groups.push_back(i);\n"
         "    }\n"
         "    mp3_groups.push_back(mp3_jobs.size());\n"
         "    std::atomic<size_t> mp3_next_group(0);\n"
         "    auto mp3_translate = [&](SpirvShaderTranslator& translator) {\n"
         "      for (size_t g; (g = mp3_next_group.fetch_add(1)) + 1 < mp3_groups.size();) {\n"
         "        for (size_t i = mp3_groups[g]; i < mp3_groups[g + 1]; ++i) {\n"
         "          translator.TranslateAnalyzedShader(*mp3_jobs[i].translation);\n"
         "        }\n"
         "      }\n"
         "    };\n"
         "    const uint32_t mp3_cpus = std::max(rex::thread::logical_processor_count(), uint32_t(2));\n"
         "    const size_t mp3_threads =\n"
         "        std::min<size_t>(std::min<uint32_t>(mp3_cpus - 1, 8), mp3_groups.size() - 1);\n"
         "    if (mp3_threads > 1) {\n"
         "      const ui::vulkan::VulkanDevice* const mp3_device = command_processor_.GetVulkanDevice();\n"
         "      const bool mp3_fsi =\n"
         "          render_target_cache_.GetPath() == RenderTargetCache::Path::kPixelShaderInterlock;\n"
         "      std::vector<std::unique_ptr<SpirvShaderTranslator>> mp3_translators;\n"
         "      std::vector<std::unique_ptr<rex::thread::Thread>> mp3_workers;\n"
         "      for (size_t i = 1; i < mp3_threads; ++i) {\n"
         "        mp3_translators.push_back(std::make_unique<SpirvShaderTranslator>(\n"
         "            SpirvShaderTranslator::Features(mp3_device),\n"
         "            render_target_cache_.msaa_2x_attachments_supported(),\n"
         "            render_target_cache_.msaa_2x_no_attachments_supported(), mp3_fsi,\n"
         "            render_target_cache_.draw_resolution_scale_x(),\n"
         "            render_target_cache_.draw_resolution_scale_y()));\n"
         "        SpirvShaderTranslator* translator = mp3_translators.back().get();\n"
         "        mp3_workers.push_back(\n"
         "            rex::thread::Thread::Create({}, [&mp3_translate, translator]() { mp3_translate(*translator); }));\n"
         "        mp3_workers.back()->set_name(\"Vulkan Shader Translation\");\n"
         "      }\n"
         "      mp3_translate(*shader_translator_);\n"
         "      for (const auto& worker : mp3_workers) rex::thread::Wait(worker.get(), false);\n"
         "    } else {\n"
         "      mp3_translate(*shader_translator_);\n"
         "    }\n"
         "    for (const Mp3Translation& job : mp3_jobs) {\n"
         "      if (!TranslateAnalyzedShader(*shader_translator_, *job.translation) && job.is_new) {\n"
         "        job.shader->DestroyTranslation(job.modification);\n"
         "      }\n"
         "    }\n"
         "    REXGPU_INFO(\"mp3: shader storage: {} shader translations for the stored pipelines in {} ms ({} threads)\",\n"
         "                mp3_jobs.size(),\n"
         "                std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -\n"
         "                                                                      mp3_start)\n"
         "                    .count(),\n"
         "                std::max<size_t>(mp3_threads, 1));\n"
         "  }\n")

    # The storage files stay open all session, and the PS5 ends the game
    # without closing them: what they gained never reached the drive
    # (mp3_002.log: 101 pipelines written, 229 read at the next start, as in
    # every session since the first). Each flush now goes to the drive.
    edit(vpc2, "#include <algorithm>\n#include <cstdio>\n",
         "#include <algorithm>\n#include <cstdio>\n#include <unistd.h>\n")
    edit(vpc2,
         "      fflush(shader_storage_file_);\n",
         "      fflush(shader_storage_file_);\n      fsync(fileno(shader_storage_file_));  // [mp3]\n")
    edit(vpc2,
         "      fflush(pipeline_storage_file_);\n",
         "      fflush(pipeline_storage_file_);\n      fsync(fileno(pipeline_storage_file_));  // [mp3]\n")

    # The next frame may be in the next buffer (Xenia's data-aware search).
    xt = xc.read_text()
    a = xt.index("void XmaContext::Decode(XMA_CONTEXT_DATA* data) {\n")
    b = xt.index("void XmaContext::ConvertFrame(")
    body = xt[a:b]
    if "mp3_next_read_offset" in body:
        print(f"already  {xc} (Decode)")
        return
    old_buf = "  uint8_t* current_input_buffer = GetCurrentInputBuffer(data);\n"
    new_buf = (old_buf +
        "  // [mp3] Read offset of the next frame from packet `index` on, which\n"
        "  // past the end of this buffer is in the next one (as Xenia).\n"
        "  const auto mp3_next_read_offset = [&](uint32_t index, uint32_t count) -> uint32_t {\n"
        "    uint8_t buffer = data->current_buffer;\n"
        "    if (index >= count) {\n"
        "      buffer ^= 1;\n      index -= count;\n"
        "    }\n"
        "    if (!data->IsInputBufferValid(buffer) || !data->GetInputBufferAddress(buffer)) {\n"
        "      return kBitsPerPacketHeader;\n    }\n"
        "    return GetNextPacketReadOffset(\n"
        "        memory()->TranslatePhysical(data->GetInputBufferAddress(buffer)), index,\n"
        "        data->GetInputBufferPacketCount(buffer));\n"
        "  };\n")
    old_ff = ("    uint32_t next_input_offset =\n"
              "        GetNextPacketReadOffset(current_input_buffer, packet_index + 1, current_input_packet_count);\n"
              "    if (next_input_offset == kBitsPerPacketHeader) {\n"
              "      SwapInputBuffer(data);\n    }\n")
    new_ff = ("    uint32_t next_input_offset =\n"
              "        mp3_next_read_offset(packet_index + 1, current_input_packet_count);\n"
              "    if (uint32_t(packet_index + 1) >= current_input_packet_count ||\n"
              "        next_input_offset == kBitsPerPacketHeader) {\n"
              "      SwapInputBuffer(data);\n    }\n")
    old_last = ("  uint32_t next_input_offset =\n"
                "      GetNextPacketReadOffset(current_input_buffer, next_packet_index, current_input_packet_count);\n\n"
                "  if (next_input_offset == kBitsPerPacketHeader) {\n"
                "    SwapInputBuffer(data);\n")
    new_last = ("  uint32_t next_input_offset =\n"
                "      mp3_next_read_offset(next_packet_index, current_input_packet_count);\n\n"
                "  if (next_packet_index >= current_input_packet_count ||\n"
                "      next_input_offset == kBitsPerPacketHeader) {\n"
                "    SwapInputBuffer(data);\n  }\n"
                "  if (next_input_offset == kBitsPerPacketHeader) {\n")
    for old, new in ((old_buf, new_buf), (old_ff, new_ff), (old_last, new_last)):
        if body.count(old) != 1:
            sys.exit(f"patch_framework: expected 1 of {old!r} in Decode, found {body.count(old)}")
        body = body.replace(old, new)
    n = [0]
    def site(m):
        n[0] += 1
        return f"data->error_status = 4, ++mp3_xma_err[{n[0] - 1}];"
    body = re.sub(r"data->error_status = 4;", site, body)
    if not 8 <= n[0] <= 16:
        sys.exit(f"patch_framework: {n[0]} error sites in Decode")
    xc.write_text(xt[:a] + body + xt[b:])
    print(f"patched  {xc} (Decode: next-buffer search, {n[0]} error sites counted)")


if __name__ == "__main__":
    main()
