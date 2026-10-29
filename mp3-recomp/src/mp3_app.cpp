// Max Payne 3 - ReXGlue recompiled app (see mp3_app.h).
#include "mp3_app.h"

#include <cstdio>
#include <system_error>

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/platform.h>
#include <rex/system/kernel_state.h>

#include "mp3_init.h"
#include "ps5/mp3_install.h"
#include "ps5/mp3_menu.h"
#include "ps5/mp3_trophies_game.h"

namespace fs = std::filesystem;

namespace {

#if REX_PLATFORM_PS5
const fs::path kAppRoot("/app0");
#endif

// The app's own folder: /app0 on the PS5, beside the program elsewhere.
fs::path AppRoot(const rex::PathConfig& defaults) {
#if REX_PLATFORM_PS5
  (void)defaults;
  return kAppRoot;
#else
  return defaults.config_path.parent_path();
#endif
}

fs::path g_app_root;

// The disc the game runs from: disc.txt in the app's folder ("1" or "2",
// written when the game asks for the other disc), disc 1 without it.
uint32_t SelectedDisc(const fs::path& root) {
  uint32_t disc = 1;
  if (std::FILE* f = std::fopen((root / "disc.txt").string().c_str(), "rb")) {
    if (std::fgetc(f) == '2') disc = 2;
    std::fclose(f);
  }
  return disc;
}

// Disc 1's files are in game/, disc 2's in game2/.
fs::path DiscFolder(const fs::path& root, uint32_t disc) { return root / (disc == 2 ? "game2" : "game"); }

}  // namespace

namespace {
rex::PathConfig ResolvePaths(const rex::PathConfig& defaults);
}  // namespace

#if REX_PLATFORM_PS5
extern "C" int sceSystemServiceLoadExec(const char* path, char* const argv[]);
#endif

// The game asked for the other disc (XamSwapDisc, patched in by
// ci/patch_framework.py): note it in disc.txt and start again on its files.
// The game then loads the save it made before asking.
extern "C" void rex_app_swap_disc(uint32_t disc_number) {
  if (g_app_root.empty() || (disc_number != 1 && disc_number != 2)) return;
  std::error_code ec;
  if (!fs::exists(DiscFolder(g_app_root, disc_number) / "default.xex", ec)) {
    REXLOG_ERROR("Max Payne 3: the game asked for disc {}, but {} has no default.xex (see README.txt)",
                 disc_number, DiscFolder(g_app_root, disc_number).string());
    return;
  }
  if (std::FILE* f = std::fopen((g_app_root / "disc.txt").string().c_str(), "wb")) {
    std::fputc(disc_number == 2 ? '2' : '1', f);
    std::fclose(f);
  }
  REXLOG_INFO("Max Payne 3: disc {} - starting again on its files", disc_number);
#if REX_PLATFORM_PS5
  rex::FlushLogging();
  char* args[] = {nullptr};
  const int rc = sceSystemServiceLoadExec("/app0/eboot.bin", args);
  REXLOG_ERROR("Max Payne 3: restarting for disc {} failed ({:#x})", disc_number, uint32_t(rc));
#else
  REXLOG_WARN("Max Payne 3: start the app again to play disc {}", disc_number);
#endif
}

std::unique_ptr<rex::ui::WindowedApp> Mp3App::Create(rex::ui::WindowedAppContext& ctx) {
  return std::unique_ptr<Mp3App>(new Mp3App(ctx, "mp3", PPCImageConfig));
}

std::optional<rex::PathConfig> Mp3App::OnFinalizePaths(const rex::PathConfig& defaults,
                                                       std::function<void(rex::PathConfig)> resume) {
#if REX_PLATFORM_PS5
  // The menu first (mp3_menu.cpp); PLAY carries on with the start, on the
  // disc it chose (disc.txt).
  mp3ps5::ShowMenu(imgui_drawer(), immediate_drawer(), AppRoot(defaults),
                   [defaults, resume](mp3ps5::MenuChoice) { resume(ResolvePaths(defaults)); });
  return std::nullopt;
#else
  (void)resume;
  return ResolvePaths(defaults);
#endif
}

void Mp3App::OnConfigureFonts(ImFontAtlas* atlas) {
#if REX_PLATFORM_PS5
  mp3ps5::ConfigureMenuFonts(atlas);
#else
  (void)atlas;
#endif
}

std::unique_ptr<rex::ui::AchievementNotificationDialog> Mp3App::CreateAchievementNotificationDialog() {
#if REX_PLATFORM_PS5
  // (an unlock as a PS5 trophy: ps5/mp3_trophies_game.h)
  return std::make_unique<mp3ps5::TrophyPopup>(imgui_drawer(), immediate_drawer(), runtime());
#else
  return rex::ReXApp::CreateAchievementNotificationDialog();
#endif
}

// The game's frame lock (title update 9's default.xex): the int at 8312C288,
// 2 in the image. At start (821FF48C) it becomes Direct3D's present interval
// (vblanks a frame), and every frame 821FEBB8 sleeps until lock * 1/60 s
// - 3 ms have passed since the last one (30.3 ms at 2: the 33 fps the game
// held whatever the vblank rate). 1 lifts both: 60 fps, paced by the TV's
// vblanks. Nothing in the game writes it, so it is set once, before the game
// runs.
REXCVAR_DEFINE_INT32(mp3_frame_lock, 2, "Max Payne 3",
                     "Vblanks a frame: 2 is the Xbox 360's 30 fps, 1 is 60 fps")
    .range(1, 4);

void Mp3App::OnPostLaunchModule(rex::system::XThread* thread) {
  rex::ReXApp::OnPostLaunchModule(thread);
#if REX_PLATFORM_PS5
  // (saves the menu imported before the game made its profile folder: into it now)
  if (auto* kernel = REX_KERNEL_STATE(); kernel && kernel->user_profile() && !g_app_root.empty())
    mp3ps5::install::PlacePendingSaves(g_app_root / "saves", kernel->user_profile()->xuid());
#endif
  constexpr uint32_t kFrameLock = 0x8312C288;
  const uint32_t lock = uint32_t(REXCVAR_GET(mp3_frame_lock));
  auto* memory = REX_KERNEL_MEMORY();
  auto* p = memory ? memory->TranslateVirtual<uint8_t*>(kFrameLock) : nullptr;
  if (!p) return;
  const uint32_t was = rex::memory::load_and_swap<uint32_t>(p);
  if (was != 2) {
    // (another build of the game: leave it alone)
    REXLOG_WARN("Max Payne 3: frame lock at {:08X} is {}, not 2: left as it is", kFrameLock, was);
    return;
  }
  rex::memory::store_and_swap<uint32_t>(p, lock);
  REXLOG_INFO("Max Payne 3: frame lock {} ({} fps)", lock, 60 / lock);
}

void Mp3App::OnPostSetup() {
#if REX_PLATFORM_PS5
  // The achievements' list and pictures, for the menu's Trophies page.
  if (runtime()) mp3ps5::WriteTrophyCatalog(runtime(), runtime()->user_data_root());
#endif
}

namespace {

// The game's folders: the disc's (disc.txt), its update/, dlc/ and saves/.
rex::PathConfig ResolvePaths(const rex::PathConfig& defaults) {
  auto paths = defaults;
  const fs::path root = AppRoot(defaults);
  std::error_code ec;
  g_app_root = root;
  if (paths.game_data_root.empty()) {
    uint32_t disc = SelectedDisc(root);
    if (disc == 2 && !fs::exists(DiscFolder(root, 2) / "default.xex", ec)) {
      REXLOG_WARN("Max Payne 3: disc.txt says disc 2, but game2/ has no default.xex: disc 1");
      disc = 1;
    }
    paths.game_data_root = DiscFolder(root, disc);
    REXLOG_INFO("Max Payne 3: disc {}", disc);
  }
  if (paths.update_data_root.empty() && fs::is_directory(paths.game_data_root / "update", ec))
    paths.update_data_root = paths.game_data_root / "update";
  if (paths.marketplace_content_root.empty()) paths.marketplace_content_root = root / "dlc";
  if (paths.saved_game_root.empty()) paths.saved_game_root = root / "saves";
  fs::create_directories(paths.saved_game_root, ec);
  fs::create_directories(paths.marketplace_content_root, ec);

  REXLOG_INFO("Max Payne 3: game {}, update {}, dlc {}, saves {}", paths.game_data_root.string(),
              paths.update_data_root.string(), paths.marketplace_content_root.string(),
              paths.saved_game_root.string());
  if (!fs::exists(paths.game_data_root / "default.xex", ec))
    REXLOG_ERROR("Max Payne 3: no default.xex in {} (copy that disc's files there, see README.txt)",
                 paths.game_data_root.string());
  if (!fs::exists(paths.game_data_root / "default.xexp", ec))
    REXLOG_ERROR("Max Payne 3: no default.xexp in {} (title update 9's disc001/ or disc002/default.xexp)",
                 paths.game_data_root.string());
  return paths;
}

}  // namespace

void Mp3App::OnPreSetup(rex::RuntimeConfig& config) {
  // The generic Xenos GPU (Xenia's Vulkan backend): no game-specific renderer.
  if (!config.graphics && config.gpu_plugin.empty()) config.gpu_plugin = "xenos";
  // Max Payne 3 on Xenia (xenia-canary/game-compatibility#42): gameplay draws
  // only the HUD until resolves are read back to the CPU ("fast": the previous
  // frame's). Its RAGE textures also use fetch constants typed "invalid",
  // which are dropped (692 in one session) unless allowed.
  // As launch flags: they outrank the configuration file and also reach a GPU
  // module that registers its flags later.
  rex::cvar::SetLaunchFlag("readback_resolve", "fast");
  rex::cvar::SetLaunchFlag("gpu_allow_invalid_fetch_constants", "true");
  // The 360's EDRAM emulated exactly (fragment shader interlock), as Xenia's
  // D3D12 ROV path where the game is verified playable. On host render
  // targets the deferred lighting stencil-tests against depth/stencil that
  // the game reinterprets between layouts (1280x720 1x / 640 2x MSAA at the
  // same EDRAM), and every light came out black. mp3.toml beside eboot.bin
  // can set render_target_path_vulkan = "host" to go back.
  if (!rex::cvar::HasNonDefaultValue("render_target_path_vulkan")) {
    rex::cvar::SetLaunchFlag("render_target_path_vulkan", "fsi");
  }
  // A pipeline met for the first time is built by the creation threads while its draws are
  // skipped (ci/patch_framework.py). The frames are shown meanwhile: by default one with a
  // skipped draw wasn't, and the picture froze until every pipeline of a new area was built.
  rex::cvar::SetLaunchFlag("vulkan_async_skip_incomplete_frames", "false");
  // Three creation threads, not 3/4 of the CPUs (13): in a burst (96 pipelines in the second
  // from the chapter 1 cutscene to gameplay, mp3_003.log) all of them compiled on the game's
  // cores at once and starved its threads - the pauses, with gaps in the sound at the same
  // moments. Three leave the game its cores and build a burst like that in about half a second.
  if (!rex::cvar::HasNonDefaultValue("vulkan_pipeline_creation_threads")) {
    rex::cvar::SetLaunchFlag("vulkan_pipeline_creation_threads", "3");
  }
  REXLOG_INFO("Max Payne 3: readback_resolve={} readback_memexport={} gpu_allow_invalid_fetch_constants={} "
              "render_target_path_vulkan={}",
              rex::cvar::GetFlagByName("readback_resolve"), rex::cvar::GetFlagByName("readback_memexport"),
              rex::cvar::GetFlagByName("gpu_allow_invalid_fetch_constants"),
              rex::cvar::GetFlagByName("render_target_path_vulkan"));
}
