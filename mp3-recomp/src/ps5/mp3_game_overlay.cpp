// Max Payne 3 PS5 - the frame rate counter and the DualSense's features (see
// mp3_game_overlay.h).
#include "mp3_game_overlay.h"

#if REX_PLATFORM_PS5

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include <SDL3/SDL_timer.h>
#include <imgui.h>
#include <rex/cvar.h>
#include <rex/frame_stats.h>
#include <rex/input/sony_feedback.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include "mp3_menu.h"

REXCVAR_DEFINE_STRING(mp3_fps_counter, "off", "Max Payne 3",
                      "The frame rate counter while playing: off, simple (frames a second and the frame time) or "
                      "detailed (also the slowest frames and a graph)");
REXCVAR_DECLARE(int32_t, mp3_frame_lock);

namespace mp3ps5 {

namespace {

std::unique_ptr<GameOverlay> g_overlay;

// green at 60 or better, white at 30, then yellow, then red
ImU32 RateColor(float ms) {
  return ms <= 17.5f ? IM_COL32(120, 230, 140, 255)
         : ms <= 34.5f ? IM_COL32(244, 245, 247, 255)
         : ms <= 50.5f ? IM_COL32(245, 200, 80, 255)
                       : IM_COL32(240, 90, 80, 255);
}

// ---- the game's state, read from its memory (title update 9's default.xex) ----
//
// The local player's ped: *(*(832E74F4) + 4) (sub_822BC8C8(0), which
// GET_PLAYER_PED and GET_PLAYER_ID use). In the ped (GET_PED_HEALTH,
// GET_PED_MAX_HEALTH, IS_PED_SHOOTING): its health, a float at +0x358, the
// most it can have at +0x1890, and "shooting", bit 3 of the word at +0x183C.
// Bullet time's meter (GET_ADRENALINE_AMT): a float at *(83220BBC) + 0xC; it
// drains in bullet time and in a shootdodge. The pause menu (IS_PAUSE_MENU_ACTIVE):
// the byte at 83286662. A cutscene: the cutscene manager, *(832E9018), active at
// +0x130 (HAS_CUTSCENE_FINISHED).
struct Guest {
  rex::memory::Memory* memory;

  // (a page that's there: a pointer read from the game may be stale)
  bool Readable(uint32_t address) const {
    if (address < 0x40000000u) return false;
    auto* heap = memory->LookupHeap(address);
    uint32_t protect = 0;
    return heap && heap->QueryProtect(address, &protect) && (protect & rex::memory::kMemoryProtectRead);
  }
  uint32_t U32(uint32_t address) const {
    return rex::memory::load_and_swap<uint32_t>(memory->TranslateVirtual<uint8_t*>(address));
  }
  uint8_t U8(uint32_t address) const { return *memory->TranslateVirtual<uint8_t*>(address); }
  float F32(uint32_t address) const {
    const uint32_t bits = U32(address);
    float f;
    std::memcpy(&f, &bits, sizeof(f));
    return std::isfinite(f) ? f : 0.0f;
  }
  uint32_t Pointer(uint32_t address) const {
    const uint32_t p = U32(address);
    return Readable(p) ? p : 0;
  }
};

std::array<uint8_t, 3> Mix(std::array<uint8_t, 3> a, std::array<uint8_t, 3> b, float t) {
  t = std::clamp(t, 0.0f, 1.0f);
  return {uint8_t(a[0] + (b[0] - a[0]) * t), uint8_t(a[1] + (b[1] - a[1]) * t), uint8_t(a[2] + (b[2] - a[2]) * t)};
}

}  // namespace

GameOverlay::GameOverlay(rex::ui::ImGuiDrawer* drawer) : ImGuiDialog(drawer) {
  window_start_ns_ = rex::frame_stats::NowNs();
  window_frames_ = rex::frame_stats::g_presents.load(std::memory_order_acquire);
}

void GameOverlay::Sample() {
  const int64_t now = rex::frame_stats::NowNs();
  const uint64_t frames = rex::frame_stats::g_presents.load(std::memory_order_acquire);
  const int64_t span = now - window_start_ns_;
  if (span < 500'000'000) return;
  const uint64_t n = frames - window_frames_;
  fps_ = float(double(n) * 1e9 / double(span));
  ms_ = n ? float(double(span) / 1e6 / double(n)) : 0.0f;
  window_start_ns_ = now;
  window_frames_ = frames;
  // the slowest frames of the last few seconds: 1% low and the worst one
  const uint32_t count = uint32_t(std::min<uint64_t>(frames > 1 ? frames - 1 : 0, rex::frame_stats::kHistory));
  std::vector<uint32_t> times;
  times.reserve(count);
  for (uint32_t i = 0; i < count; ++i) {
    const uint32_t us = rex::frame_stats::g_history_us[(frames - 1 - i) % rex::frame_stats::kHistory].load(
        std::memory_order_relaxed);
    if (us) times.push_back(us);
  }
  if (times.empty()) {
    low_fps_ = worst_ms_ = 0;
    return;
  }
  std::sort(times.begin(), times.end());
  const uint32_t p99 = times[size_t(double(times.size() - 1) * 0.99)];
  low_fps_ = p99 ? float(1e6 / double(p99)) : 0.0f;
  worst_ms_ = float(times.back()) / 1000.0f;
}

void GameOverlay::Feedback() {
  namespace sony = rex::input::sony;
  auto& service = sony::GetService();
  const sony::Device device = service.ReadDevice(0);
  if (!device.generation) return;
  auto* kernel = REX_KERNEL_STATE();
  if (!kernel || !kernel->memory()) return;
  const Guest g{kernel->memory()};
  const uint64_t now = SDL_GetTicks();

  const uint32_t player = g.Pointer(0x832E74F4);
  const uint32_t ped = player ? g.Pointer(player + 4) : 0;
  const uint32_t cutscene = g.Pointer(0x832E9018);
  const bool paused = g.U8(0x83286662) != 0;
  const bool in_cutscene = cutscene && g.U8(cutscene + 0x130) != 0;
  const float health = ped ? g.F32(ped + 0x358) : 0.0f;
  const float most = ped ? g.F32(ped + 0x1890) : 0.0f;
  const bool shooting = ped && (g.U32(ped + 0x183C) & 0x8);
  const uint32_t adrenaline_at = g.Pointer(0x83220BBC);
  const float adrenaline = adrenaline_at ? g.F32(adrenaline_at + 0xC) : 0.0f;

  sony::GameState state{};
  state.active = ped && health > 0 && most > 0 && !paused && !in_cutscene;
  state.player_identity = ped;
  if (state.active) {
    state.trigger_context = sony::TriggerContext::kWeapon;
    state.aiming = device.left_trigger > 40;
    state.can_fire = true;
    if (ped == ped_) {
      // hit: the health dropped; a painkiller: it rose by a tenth or more
      const float drop = health_ - health;
      if (drop > most * 0.005f) {
        service.Emit(0, device.generation, sony::Event::kDamage, std::min(1.0f, 0.4f + 4.0f * drop / most), now);
        hit_until_ms_ = now + 220;
      } else if (-drop > most * 0.1f) {
        heal_until_ms_ = now + 450;
      }
      // a shot: as "shooting" starts, then every tenth of a second while it lasts
      if (shooting && (!shooting_ || now - shot_ms_ >= 100)) {
        service.Emit(0, device.generation, sony::Event::kShot, 1.0f, now);
        shot_ms_ = now;
      }
      // bullet time or a shootdodge: the meter draining
      if (adrenaline < adrenaline_ - 0.0005f) bullet_time_until_ms_ = now + 150;
    }
    // the light bar: warm white with full health, to red as it runs out (pulsing
    // below a quarter); red for a hit, green for a painkiller, blue in bullet time
    const float ratio = std::clamp(health / most, 0.0f, 1.0f);
    std::array<uint8_t, 3> color = Mix({255, 0, 0}, {255, 190, 130}, (ratio - 0.15f) / 0.75f);
    if (ratio < 0.25f) {
      const float beat = 0.5f + 0.5f * std::sin(float(now % 1000) / 1000.0f * 6.2831853f);
      color = Mix({70, 0, 0}, {255, 0, 0}, beat);
    }
    if (now < bullet_time_until_ms_) color = {40, 130, 255};
    if (now < heal_until_ms_) color = {40, 255, 90};
    if (now < hit_until_ms_) color = {255, 0, 0};
    state.has_color = true;
    state.color = color;
  }
  service.Publish(0, device.generation, state, now);
  ped_ = state.active ? ped : 0;
  health_ = health;
  shooting_ = shooting;
  adrenaline_ = adrenaline;
}

void GameOverlay::OnDraw(ImGuiIO& io) {
  Feedback();
  const std::string mode = REXCVAR_GET(mp3_fps_counter);
  if (mode != "simple" && mode != "detailed") return;
  const bool simple = mode == "simple";
  Sample();
  const ImVec2 size = io.DisplaySize;
  const float s = std::min(size.x / 1920.0f, size.y / 1080.0f);
  ImDrawList* dl = ImGui::GetForegroundDrawList();
  ImFont* bold = MenuItemFont() ? MenuItemFont() : ImGui::GetFont();
  ImFont* body = MenuBodyFont() ? MenuBodyFont() : ImGui::GetFont();
  auto P = [&](float x, float y) { return ImVec2(x * s, y * s); };

  std::vector<std::string> info;
  if (!simple) {
    info.push_back(std::string("1280x720  \xC2\xB7  ") +
                   (REXCVAR_GET(mp3_frame_lock) == 1 ? "60 FPS mode" : "30 FPS mode (locked)"));
  }
  const float w = simple ? 196 : 264, pad = 12, line_h = 19;
  const float graph_h = simple ? 0 : 48;
  const float h = simple ? pad + 36 + pad - 4 : pad + 36 + 20 + float(info.size()) * line_h + 10 + graph_h + pad;
  const float x = 24, y = 24;  // (the top-left corner, which the game's HUD leaves empty)
  dl->AddRectFilled(P(x, y), P(x + w, y + h), IM_COL32(0, 0, 0, 170), 8 * s);
  char big[16], unit[] = "FPS", ms[32], small[96];
  if (fps_ > 0) {
    std::snprintf(big, sizeof(big), "%.0f", fps_);
    std::snprintf(ms, sizeof(ms), "%.1f ms", ms_);
    std::snprintf(small, sizeof(small), "1%% low %.0f  \xC2\xB7  worst frame %.0f ms", low_fps_, worst_ms_);
  } else {
    std::snprintf(big, sizeof(big), "--");
    ms[0] = small[0] = 0;
  }
  const ImU32 col = fps_ > 0 ? RateColor(ms_) : IM_COL32(244, 245, 247, 255);
  const float big_size = 36 * s;
  const ImVec2 bsz = bold->CalcTextSizeA(big_size, 1e9f, 0, big);
  dl->AddText(bold, big_size, P(x + pad, y + pad - 5), col, big);
  dl->AddText(bold, 15 * s, ImVec2((x + pad) * s + bsz.x + 6 * s, (y + pad + 13) * s), IM_COL32(255, 255, 255, 170),
              unit);
  const ImVec2 msz = bold->CalcTextSizeA(20 * s, 1e9f, 0, ms);
  dl->AddText(bold, 20 * s, ImVec2((x + w - pad) * s - msz.x, (y + pad + 8) * s), IM_COL32(244, 245, 247, 255), ms);
  if (simple) return;
  dl->AddText(body, 14 * s, P(x + pad, y + pad + 37), IM_COL32(255, 255, 255, 175), small);
  for (size_t i = 0; i < info.size(); ++i)
    dl->AddText(body, 14 * s, P(x + pad, y + pad + 37 + 20 + float(i) * line_h), IM_COL32(255, 255, 255, 175),
                info[i].c_str());

  // the graph: one bar a frame, newest on the right, 0 to 66 ms (15 FPS) tall
  const float gx = x + pad, gy = y + pad + 36 + 20 + float(info.size()) * line_h + 10, gw = w - pad * 2;
  dl->AddRectFilled(P(gx, gy), P(gx + gw, gy + graph_h), IM_COL32(255, 255, 255, 18), 4 * s);
  constexpr float kTopMs = 66.7f;
  const uint64_t frames = rex::frame_stats::g_presents.load(std::memory_order_acquire);
  constexpr int kBars = 120;
  const float bar = gw / float(kBars);
  for (int i = 0; i < kBars && uint64_t(i) + 1 < frames; ++i) {
    const uint32_t us = rex::frame_stats::g_history_us[(frames - 1 - uint64_t(i)) % rex::frame_stats::kHistory].load(
        std::memory_order_relaxed);
    if (!us) continue;
    const float f_ms = float(us) / 1000.0f;
    const float bh = graph_h * std::min(f_ms, kTopMs) / kTopMs;
    const float bx = gx + gw - bar * float(i + 1);
    dl->AddRectFilled(P(bx, gy + graph_h - bh), P(bx + bar * 0.8f, gy + graph_h), RateColor(f_ms));
  }
  for (float line_ms : {16.7f, 33.3f}) {  // the 60 and 30 FPS marks, over the bars
    const float ly = gy + graph_h - graph_h * line_ms / kTopMs;
    dl->AddLine(P(gx, ly), P(gx + gw, ly), IM_COL32(0, 0, 0, 140), 2 * s);
    dl->AddLine(P(gx, ly), P(gx + gw, ly), IM_COL32(255, 255, 255, 90), 1 * s);
    const char* label = line_ms < 20 ? "60" : "30";
    dl->AddText(bold, 11 * s, P(gx + 3 + 1, ly - 13 + 1), IM_COL32(0, 0, 0, 200), label);
    dl->AddText(bold, 11 * s, P(gx + 3, ly - 13), IM_COL32(255, 255, 255, 200), label);
  }
}

void StartGameOverlay(rex::ui::ImGuiDrawer* drawer) {
  if (!g_overlay && drawer) g_overlay = std::make_unique<GameOverlay>(drawer);
}

}  // namespace mp3ps5

#endif  // REX_PLATFORM_PS5
