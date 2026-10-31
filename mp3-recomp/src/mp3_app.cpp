// Max Payne 3 - ReXGlue recompiled app (see mp3_app.h).
#include "mp3_app.h"

#include <cstdio>
#include <cstring>
#include <system_error>

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/platform.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include "mp3_init.h"
#include "ps5/mp3_game_overlay.h"
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

// PlayStation button icons. The Xbox 360 disc carries the PS3 version's icons too
// (xbox360.rpf: textures/buttons_ps3.xtd and controller_ps3.xtd, beside the _360 ones), so
// the game loads those: the two paths in its read-only data end in "360", which becomes
// "ps3". The PS3 dictionary names its pictures as the PS3 build does (p_cross, not a_butt),
// so the texture dictionary lookup (sub_82BC9A80: dictionary r3, the name's hash r4,
// following the parents; the texture or 0 in r3) is asked for the PS3 name of an Xbox one
// first. The rest (the sticks, the d-pad, START) have the same names in both.
REXCVAR_DEFINE_BOOL(mp3_ps_buttons, true, "Max Payne 3", "PlayStation button icons (the PS3 version's)");

// Skip any cutscene. The cutscene manager's skip check (sub_822D3350, every frame of a
// playing cutscene) does nothing in the ones the scripts lock (CUTSCENE_ENABLE_SKIP false:
// the byte at +0x39F0), mostly scenes that hand over to gameplay. With this, the skip button
// moves those to 3 s before their end instead (as Fusion Fix does on the PC: ThirteenAG's
// MaxPayne3.FusionFix, cutsceneskip.ixx), so the scripts' timed steps still run and the
// scene ends as the scripts expect. The ones the scripts let you skip are left to the game.
REXCVAR_DEFINE_BOOL(mp3_cutscene_skip, true, "Max Payne 3", "The skip button skips every cutscene");

// Skip the intro: the Rockstar logo movie the front end plays as the game starts
// (mp3_payne_pill_rockstar_logo, through the movie player's play, sub_822C4018:
// player r3, slot r4, the movie's name r5) isn't started; the front end then sees
// no movie playing and carries on (as Fusion Fix's SkipIntro on the PC).
REXCVAR_DEFINE_BOOL(mp3_skip_intro, true, "Max Payne 3", "Skip the Rockstar logo movie at the start");

namespace {

constexpr uint32_t Joaat(const char* s) {
  uint32_t h = 0;
  for (; *s; ++s) {
    h += uint32_t(*s >= 'A' && *s <= 'Z' ? *s + 32 : *s);
    h += h << 10;
    h ^= h >> 6;
  }
  h += h << 3;
  h ^= h >> 11;
  h += h << 15;
  return h;
}

struct ButtonName {
  uint32_t xbox, ps;
};
constexpr ButtonName kButtonNames[] = {
    {Joaat("a_butt"), Joaat("p_cross")},     {Joaat("b_butt"), Joaat("p_circle")},
    {Joaat("x_butt"), Joaat("p_square")},    {Joaat("y_butt"), Joaat("p_triangle")},
    {Joaat("lb_butt"), Joaat("p_l1")},       {Joaat("rb_butt"), Joaat("p_r1")},
    {Joaat("lt_butt"), Joaat("p_l2")},       {Joaat("rt_butt"), Joaat("p_r2")},
    {Joaat("back_butt"), Joaat("p_select")},
};

bool g_ps_buttons = false;
bool g_cutscene_skip = false;

// A string in the game's read-only data, changed in place (same length).
bool PatchGuestString(rex::memory::Memory* memory, uint32_t address, const char* was, const char* now) {
  const size_t n = std::strlen(was) + 1;
  auto* p = memory->TranslateVirtual<char*>(address);
  if (!p || std::memcmp(p, was, n) != 0) return false;
  auto* heap = memory->LookupHeap(address);
  uint32_t old_protect = 0;
  const uint32_t page = address & ~0xFFFFu, size = ((address + uint32_t(n) + 0xFFFF) & ~0xFFFFu) - page;
  if (heap) heap->Protect(page, size, rex::memory::kMemoryProtectRead | rex::memory::kMemoryProtectWrite, &old_protect);
  std::memcpy(p, now, n);
  if (heap && old_protect) heap->Protect(page, size, old_protect);
  return true;
}

void ApplyButtonIcons(rex::memory::Memory* memory) {
  if (!REXCVAR_GET(mp3_ps_buttons)) return;
  const bool a = PatchGuestString(memory, 0x820286C8, "platform:/textures/buttons_360", "platform:/textures/buttons_ps3");
  const bool b = PatchGuestString(memory, 0x82059A68, "controller_360", "controller_ps3");
  g_ps_buttons = a;
  REXLOG_INFO("Max Payne 3: PlayStation button icons {}{}", a ? "on" : "not found (another build?)",
              b ? "" : ", the controller picture left as it is");
}

}  // namespace

extern "C" void sub_82BC9A80(PPCContext& __restrict ctx, uint8_t* base) {
  if (g_ps_buttons) {
    const uint32_t hash = ctx.r4.u32;
    for (const ButtonName& name : kButtonNames) {
      if (name.xbox != hash) continue;
      const uint64_t dictionary = ctx.r3.u64;
      ctx.r4.u64 = name.ps;
      __imp__sub_82BC9A80(ctx, base);
      if (ctx.r3.u32) return;
      ctx.r3.u64 = dictionary;
      ctx.r4.u64 = hash;
      break;
    }
  }
  __imp__sub_82BC9A80(ctx, base);
}

extern "C" void sub_822C4018(PPCContext& __restrict ctx, uint8_t* base) {
  static constexpr char kLogo[] = "mp3_payne_pill_rockstar_logo";
  if (REXCVAR_GET(mp3_skip_intro) && ctx.r5.u32 >= 0x80000000u && ctx.r5.u32 < 0x84000000u &&
      std::memcmp(base + ctx.r5.u32, kLogo, sizeof(kLogo)) == 0) {
    REXLOG_INFO("Max Payne 3: the Rockstar logo movie skipped");
    return;
  }
  __imp__sub_822C4018(ctx, base);
}

extern "C" void sub_822D3350(PPCContext& __restrict ctx, uint8_t* base) {
  const uint32_t manager = ctx.r3.u32;
  // (inactive, or the scripts let this one be skipped: the game's own check)
  if (!g_cutscene_skip || !REX_LOAD_U8(manager + 0x130) || REX_LOAD_U8(manager + 0x39F0)) {
    __imp__sub_822D3350(ctx, base);
    return;
  }
  // The skip button, as the game's check reads it (its controls, then "just pressed"); not
  // while a multiplayer session's front end is up.
  ctx.r3.u64 = 1;
  sub_8225A320(ctx, base);
  sub_823C3F98(ctx, base);
  bool pressed = (ctx.r3.u32 & 0xFF) != 0;
  if ((REX_LOAD_U8(0x83229D6F) | REX_LOAD_U8(0x83229D70)) && REX_LOAD_U8(0x83286662)) pressed = false;
  if (!pressed) return;
  constexpr int kFps = 30, kTail = 3 * kFps;
  const int32_t start = int32_t(REX_LOAD_U32(manager + 0x2D44));
  const int32_t end = int32_t(REX_LOAD_U32(manager + 0x2D48));
  const uint32_t time_bits = REX_LOAD_U32(manager + 0x13C);
  float time;
  std::memcpy(&time, &time_bits, sizeof(time));
  const int32_t current = start + int32_t(time * kFps);
  const int32_t target = end - kTail;
  if (target <= current + kFps / 2) return;
  REX_STORE_U32(manager + 0x3520, uint32_t(target));  // the frame to seek to
  REX_STORE_U32(manager + 0x3508, 0);                 // (0: seek)
  // The cutscene's speech and streamed sounds to the same time (as the game's skip does).
  sub_822B8FD8(ctx, base);
  const uint32_t audio = REX_LOAD_U32(ctx.r3.u32 + 0x58);
  if (audio) {
    ctx.r3.u64 = audio;
    ctx.f1.f64 = double(float(target) / kFps);
    sub_828DC840(ctx, base);
    ctx.r3.u64 = audio;
    ctx.f1.f64 = double(float(target) / kFps);
    sub_828D6460(ctx, base);
  }
  REXLOG_INFO("Max Payne 3: locked cutscene skipped to frame {} of {}", target, end);
}

void Mp3App::OnPostLaunchModule(rex::system::XThread* thread) {
  rex::ReXApp::OnPostLaunchModule(thread);
#if REX_PLATFORM_PS5
  // (saves the menu imported before the game made its profile folder: into it now)
  if (auto* kernel = REX_KERNEL_STATE(); kernel && kernel->user_profile() && !g_app_root.empty())
    mp3ps5::install::PlacePendingSaves(g_app_root / "saves", kernel->user_profile()->xuid());
#endif
  auto* memory = REX_KERNEL_MEMORY();
  if (memory) ApplyButtonIcons(memory);
  g_cutscene_skip = REXCVAR_GET(mp3_cutscene_skip);
  REXLOG_INFO("Max Payne 3: skip any cutscene {}", g_cutscene_skip ? "on" : "off");
  constexpr uint32_t kFrameLock = 0x8312C288;
  const uint32_t lock = uint32_t(REXCVAR_GET(mp3_frame_lock));
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
  // The frame rate counter and the DualSense's features (ps5/mp3_game_overlay.h).
  mp3ps5::StartGameOverlay(imgui_drawer());
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
