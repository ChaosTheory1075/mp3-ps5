// Max Payne 3 PS5 - the in-game quick menu (see mp3_quick_menu.h).
//
// The controller is read where the game reads it: sub_8309DBD8, the title's XInputGetState (r3 the
// user, r4 the X_INPUT_STATE; it calls XamInputGetState). After it runs, the menu takes what was
// pressed and, while open, blanks the state the game gets.
#include "mp3_quick_menu.h"
#if REX_PLATFORM_PS5

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <cfloat>
#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_set>
#include <vector>

#include <imgui.h>
#include <rex/cvar.h>
#include <rex/input/sony_feedback.h>
#include <rex/logging.h>
#include <rex/memory.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include "mp3_init.h"
#include "mp3_menu.h"

REXCVAR_DECLARE(std::string, mp3_fps_counter);
REXCVAR_DEFINE_BOOL(mp3_quick_menu, true, "Max Payne 3",
                    "L3 + R3 opens the in-game quick menu (the cheats); off: no menu, and no cheats");

namespace mp3ps5 {
namespace {

constexpr const char* kCheatsFile = "/app0/cheats.ini";
constexpr const char* kMenuFile = "/app0/menu.ini";

// X_INPUT_GAMEPAD in the X_INPUT_STATE (big-endian): buttons, the triggers, the sticks.
constexpr uint32_t kGamepad = 4, kGamepadSize = 12, kThumbLY = 6;
constexpr uint16_t kUp = 0x0001, kDown = 0x0002, kLeft = 0x0004, kRight = 0x0008, kL3 = 0x0040, kR3 = 0x0080,
                   kCross = 0x1000, kCircle = 0x2000;
constexpr int kStickDeadzone = 16000;

enum Nav : uint8_t { kNavUp = 1, kNavDown, kNavLeft, kNavRight, kNavSelect, kNavClose };

std::unique_ptr<QuickMenu> g_menu;
std::atomic<bool> g_open{false};
std::mutex g_mutex;
std::deque<uint8_t> g_events;  // guarded by g_mutex
uint16_t g_previous = 0;       // the game thread's
bool g_swallow = false;        // blank the pad until everything is let go (after closing)

void Push(uint8_t event) {
  std::lock_guard lock(g_mutex);
  if (g_events.size() < 16) g_events.push_back(event);
}

uint8_t Take() {
  std::lock_guard lock(g_mutex);
  if (g_events.empty()) return 0;
  const uint8_t e = g_events.front();
  g_events.pop_front();
  return e;
}

uint16_t LoadBe16(uint8_t* base, uint32_t address) {
  uint16_t v;
  std::memcpy(&v, base + address, 2);
  return std::byteswap(v);
}

// One read of the controller, on the game thread.
void Filter(uint8_t* base, uint32_t state) {
  uint16_t buttons = LoadBe16(base, state + kGamepad);
  const int ly = int16_t(LoadBe16(base, state + kGamepad + kThumbLY));
  if (ly > kStickDeadzone) buttons |= kUp;  // (the left stick as the D-pad)
  if (ly < -kStickDeadzone) buttons |= kDown;
  const uint16_t pressed = buttons & ~g_previous;
  const bool combo = (buttons & (kL3 | kR3)) == (kL3 | kR3);
  const bool combo_pressed = combo && (pressed & (kL3 | kR3));
  g_previous = buttons;
  if (!g_open.load(std::memory_order_relaxed)) {
    if (combo_pressed && REXCVAR_GET(mp3_quick_menu)) {
      { std::lock_guard lock(g_mutex); g_events.clear(); }
      g_open.store(true, std::memory_order_release);
      REXLOG_INFO("Quick menu: open");
    } else if (g_swallow && !buttons) {
      g_swallow = false;
    }
    if (!g_open.load(std::memory_order_relaxed) && !g_swallow) return;
  } else {
    if (combo_pressed || (pressed & kCircle)) {
      g_open.store(false, std::memory_order_release);
      g_swallow = true;
      Push(kNavClose);
    } else {
      if (pressed & kUp) Push(kNavUp);
      if (pressed & kDown) Push(kNavDown);
      if (pressed & kLeft) Push(kNavLeft);
      if (pressed & kRight) Push(kNavRight);
      if (pressed & kCross) Push(kNavSelect);
    }
  }
  std::memset(base + state + kGamepad, 0, kGamepadSize);
}

// ---- the cheats ----

enum Cheat { kGod, kAmmo, kBulletTime, kOneShot, kIgnored, kCheatCount };
struct CheatInfo {
  const char* key;    // in cheats.ini
  const char* label;  // in the menu
};
constexpr CheatInfo kCheats[kCheatCount] = {
    {"god_mode", "God Mode"},
    {"infinite_ammo", "Infinite Ammo"},
    {"infinite_bullet_time", "Infinite Bullet Time"},
    {"one_shot_kills", "One-Shot Kills"},
    {"enemies_ignore_you", "Enemies Ignore You"},
};
std::array<std::atomic<bool>, kCheatCount> g_on{};

void LoadCheats() {
  std::FILE* f = std::fopen(kCheatsFile, "rb");
  if (!f) return;
  char line[256];
  while (std::fgets(line, sizeof(line), f)) {
    char key[64] = {};
    int value = 0;
    if (std::sscanf(line, " %63[a-z_] = %d", key, &value) != 2) continue;
    for (int i = 0; i < kCheatCount; ++i)
      if (key == std::string(kCheats[i].key)) g_on[i].store(value != 0);
  }
  std::fclose(f);
}

void SaveCheats() {
  std::FILE* f = std::fopen(kCheatsFile, "wb");
  if (!f) {
    REXLOG_WARN("Cheats: cannot write {}", kCheatsFile);
    return;
  }
  for (int i = 0; i < kCheatCount; ++i) std::fprintf(f, "%s = %d\n", kCheats[i].key, g_on[i].load() ? 1 : 0);
  std::fclose(f);
}

// The game's memory (big-endian), from the UI thread.
struct Guest {
  rex::memory::Memory* memory;

  // (a page that's there: a pointer read from the game may be stale)
  bool Readable(uint32_t address) const {
    if (address < 0x40000000u) return false;
    auto* heap = memory->LookupHeap(address);
    uint32_t protect = 0;
    return heap && heap->QueryProtect(address, &protect) && (protect & rex::memory::kMemoryProtectWrite);
  }
  uint8_t* At(uint32_t address) const { return memory->TranslateVirtual<uint8_t*>(address); }
  uint32_t U32(uint32_t address) const { return rex::memory::load_and_swap<uint32_t>(At(address)); }
  void SetU32(uint32_t address, uint32_t v) const { rex::memory::store_and_swap<uint32_t>(At(address), v); }
  uint8_t U8(uint32_t address) const { return *At(address); }
  void SetU8(uint32_t address, uint8_t v) const { *At(address) = v; }
  float F32(uint32_t address) const { return std::bit_cast<float>(U32(address)); }
  void SetF32(uint32_t address, float v) const { SetU32(address, std::bit_cast<uint32_t>(v)); }
  uint32_t Pointer(uint32_t address) const {
    const uint32_t p = U32(address);
    return Readable(p) ? p : 0;
  }
};

constexpr uint32_t kPlayer = 0x832E74F4;      // *(+4): the local player's ped
constexpr uint32_t kPedPool = 0x832E74DC;     // the peds: storage, slot flags, count, item size
constexpr uint32_t kAdrenaline = 0x83220BBC;  // the bullet time meter's: floats at +8 and +0xC
constexpr uint32_t kIgnoredFlag = 0x8352FEF5;  // "playerignored"
constexpr uint32_t kInvincibleWord = 0x1A8, kInvincibleBit = 0x00080000;
constexpr uint32_t kAmmoByte = 0x5C0, kConsumesAmmo = 0x20;
constexpr uint32_t kOneShotWord = 0x1804, kOneShotBit = 0x00400000;
constexpr uint32_t kHealth = 0x358, kMaxHealth = 0x1890;

// What the cheats changed, to put back when they're switched off (the UI thread's).
struct Applied {
  uint32_t god_ped = 0, ammo_ped = 0;
  bool ignored = false;
  std::unordered_set<uint32_t> one_shot;  // the peds whose flag the cheat set
} g_applied;

// The live peds in the pool (slots without the high "free" bit).
template <typename F>
void ForEachPed(const Guest& g, F&& f) {
  const uint32_t pool = g.Pointer(kPedPool);
  if (!pool) return;
  const uint32_t storage = g.U32(pool), flags = g.U32(pool + 4), count = g.U32(pool + 8), size = g.U32(pool + 0xC);
  if (!g.Readable(storage) || !g.Readable(flags) || count > 4096 || size < 0x100 || size > 0x10000) return;
  for (uint32_t i = 0; i < count; ++i)
    if (!(g.U8(flags + i) & 0x80)) f(storage + i * size);
}

// Each frame: the cheats that are on held, those switched off undone.
void ApplyCheats() {
  auto* kernel = REX_KERNEL_STATE();
  if (!kernel || !kernel->memory()) return;
  const Guest g{kernel->memory()};
  const bool multiplayer = (g.U8(0x83229D6F) | g.U8(0x83229D70)) != 0;
  const uint32_t player = g.Pointer(kPlayer);
  // (the menu switched off in the app's settings: no cheats either)
  const uint32_t ped = player && !multiplayer && REXCVAR_GET(mp3_quick_menu) ? g.Pointer(player + 4) : 0;
  const bool god = ped && g_on[kGod], ammo = ped && g_on[kAmmo], one_shot = ped && g_on[kOneShot];

  if (god) {
    const uint32_t w = g.U32(ped + kInvincibleWord);
    if (!(w & kInvincibleBit)) {
      g.SetU32(ped + kInvincibleWord, w | kInvincibleBit);
      g_applied.god_ped = ped;
    }
    const float health = g.F32(ped + kHealth), most = g.F32(ped + kMaxHealth);
    if (health > 0 && most > 0 && health < most) g.SetF32(ped + kHealth, most);  // (never a revive)
  }
  if (g_applied.god_ped && (!god || g_applied.god_ped != ped)) {
    if (g.Readable(g_applied.god_ped))
      g.SetU32(g_applied.god_ped + kInvincibleWord, g.U32(g_applied.god_ped + kInvincibleWord) & ~kInvincibleBit);
    g_applied.god_ped = 0;
  }

  if (ammo) {
    const uint8_t b = g.U8(ped + kAmmoByte);
    if (b & kConsumesAmmo) {
      g.SetU8(ped + kAmmoByte, uint8_t(b & ~kConsumesAmmo));
      g_applied.ammo_ped = ped;
    }
  }
  if (g_applied.ammo_ped && (!ammo || g_applied.ammo_ped != ped)) {
    if (g.Readable(g_applied.ammo_ped))
      g.SetU8(g_applied.ammo_ped + kAmmoByte, uint8_t(g.U8(g_applied.ammo_ped + kAmmoByte) | kConsumesAmmo));
    g_applied.ammo_ped = 0;
  }

  if (ped && g_on[kBulletTime]) {
    if (const uint32_t meter = g.Pointer(kAdrenaline)) {
      g.SetF32(meter + 8, 1.0f);
      g.SetF32(meter + 0xC, 1.0f);
    }
  }

  const bool ignored = ped && g_on[kIgnored];
  if (ignored && !g.U8(kIgnoredFlag)) {
    g.SetU8(kIgnoredFlag, 1);
    g_applied.ignored = true;
  } else if (!ignored && g_applied.ignored) {
    g.SetU8(kIgnoredFlag, 0);
    g_applied.ignored = false;
  }

  if (one_shot) {
    ForEachPed(g, [&](uint32_t other) {
      if (other == ped) return;
      const uint32_t w = g.U32(other + kOneShotWord);
      if (!(w & kOneShotBit)) {
        g.SetU32(other + kOneShotWord, w | kOneShotBit);
        g_applied.one_shot.insert(other);
      }
    });
  } else if (!g_applied.one_shot.empty()) {
    // (only the live peds the cheat marked; a freed slot's ped is gone anyway)
    ForEachPed(g, [&](uint32_t other) {
      if (g_applied.one_shot.count(other))
        g.SetU32(other + kOneShotWord, g.U32(other + kOneShotWord) & ~kOneShotBit);
    });
    g_applied.one_shot.clear();
  }
}

// menu.ini's `key` set to `value`, the other lines kept.
void SetMenuValue(const char* key, int value) {
  std::vector<std::string> lines;
  if (std::FILE* f = std::fopen(kMenuFile, "rb")) {
    char line[256];
    while (std::fgets(line, sizeof(line), f)) lines.emplace_back(line);
    std::fclose(f);
  }
  const std::string prefix = std::string(key) + " =";
  const std::string entry = prefix + " " + std::to_string(value) + "\n";
  bool found = false;
  for (std::string& l : lines)
    if (l.rfind(prefix, 0) == 0) l = entry, found = true;
  if (!found) lines.push_back(entry);
  if (std::FILE* f = std::fopen(kMenuFile, "wb")) {
    for (const std::string& l : lines) std::fprintf(f, "%s", l.c_str());  // (fprintf: already imported)
    std::fclose(f);
  }
}

// ---- the rows ----

struct Row {
  const char* section;  // a heading over the row, or nullptr
  const char* label;
  int cheat;  // the cheat, or -1 the FPS counter, -2 the DualSense features
};

std::vector<Row> Rows() {
  std::vector<Row> rows;
  for (int i = 0; i < kCheatCount; ++i) rows.push_back({i == 0 ? "CHEATS" : nullptr, kCheats[i].label, i});
  rows.push_back({"DISPLAY", "FPS Counter", -1});
  rows.push_back({"CONTROLLER", "DualSense Features", -2});
  return rows;
}

int CounterMode() {
  const std::string m = REXCVAR_GET(mp3_fps_counter);
  return m == "detailed" ? 2 : m == "simple" ? 1 : 0;
}

std::string ValueOf(const Row& row) {
  if (row.cheat >= 0) return g_on[row.cheat].load() ? "ON" : "OFF";
  if (row.cheat == -1) return CounterMode() == 2 ? "DETAILED" : CounterMode() == 1 ? "SIMPLE" : "OFF";
  return rex::input::sony::GetOptions().enabled ? "ON" : "OFF";
}

// One step forward (or back) of the row's value.
void Change(const Row& row, bool back) {
  if (row.cheat >= 0) {
    g_on[row.cheat].store(!g_on[row.cheat].load());
    REXLOG_INFO("Quick menu: {} {}", kCheats[row.cheat].label, g_on[row.cheat].load() ? "on" : "off");
    SaveCheats();
  } else if (row.cheat == -1) {
    static constexpr const char* kModes[] = {"off", "simple", "detailed"};
    const int mode = (CounterMode() + (back ? 2 : 1)) % 3;
    rex::cvar::SetFlagByName("mp3_fps_counter", kModes[mode]);
    SetMenuValue("fps_counter", mode);
  } else {
    const bool on = !rex::input::sony::GetOptions().enabled;
    rex::input::sony::SetFeaturesEnabled(on);
    SetMenuValue("dualsense", on ? 1 : 0);
  }
}

constexpr ImU32 kWhite = IM_COL32(236, 232, 226, 255), kGrey = IM_COL32(150, 146, 140, 255),
                kRed = IM_COL32(196, 22, 28, 255);

ImU32 Fade(ImU32 c, float a) { return (c & 0x00FFFFFFu) | (ImU32(float(c >> 24) * a) << 24); }

}  // namespace

QuickMenu::QuickMenu(rex::ui::ImGuiDrawer* drawer) : ImGuiDialog(drawer) {}

bool QuickMenu::WantsContinuousRepaint() const { return g_open.load(std::memory_order_relaxed) || shown_ > 0; }

void QuickMenu::OnDraw(ImGuiIO& io) {
  ApplyCheats();
  const bool open = g_open.load(std::memory_order_acquire);
  shown_ = std::clamp(shown_ + (open ? 1.0f : -1.0f) * std::min(io.DeltaTime, 0.1f) * 8.0f, 0.0f, 1.0f);
  const std::vector<Row> rows = Rows();
  for (uint8_t e; (e = Take());) {
    if (e == kNavUp) row_ = (row_ + int(rows.size()) - 1) % int(rows.size());
    if (e == kNavDown) row_ = (row_ + 1) % int(rows.size());
    if (e == kNavLeft) Change(rows[size_t(row_)], true);
    if (e == kNavRight || e == kNavSelect) Change(rows[size_t(row_)], false);
  }
  if (shown_ <= 0) return;

  const ImVec2 size = io.DisplaySize;
  const float u = std::min(size.x / 1920.0f, size.y / 1080.0f), a = shown_;
  ImDrawList* dl = ImGui::GetForegroundDrawList();
  ImFont* item = MenuItemFont() ? MenuItemFont() : ImGui::GetFont();
  ImFont* body = MenuBodyFont() ? MenuBodyFont() : ImGui::GetFont();
  int headings = 0;
  for (const Row& r : rows) headings += r.section ? 1 : 0;
  const float row_h = 40 * u, head_h = 44 * u, w = 760 * u;
  const float h = 110 * u + rows.size() * row_h + headings * head_h + 70 * u;
  const float x = (size.x - w) * 0.5f, y0 = (size.y - h) * 0.5f;
  dl->AddRectFilled(ImVec2(0, 0), size, Fade(IM_COL32(0, 0, 0, 110), a));
  dl->AddRectFilled(ImVec2(x, y0), ImVec2(x + w, y0 + h), Fade(IM_COL32(10, 9, 9, 235), a), 10 * u);
  dl->AddRectFilled(ImVec2(x, y0), ImVec2(x + 8 * u, y0 + h), Fade(kRed, a), 10 * u, ImDrawFlags_RoundCornersLeft);
  dl->AddText(item, 46 * u, ImVec2(x + 40 * u, y0 + 26 * u), Fade(kWhite, a), "QUICK MENU");
  dl->AddText(body, 20 * u, ImVec2(x + 330 * u, y0 + 44 * u), Fade(kGrey, a), "(the cheats act in single player)");
  float y = y0 + 100 * u;
  for (size_t i = 0; i < rows.size(); ++i) {
    const Row& r = rows[i];
    if (r.section) {
      y += 10 * u;
      dl->AddText(body, 20 * u, ImVec2(x + 40 * u, y), Fade(kRed, a), r.section);
      dl->AddRectFilled(ImVec2(x + 40 * u, y + 28 * u), ImVec2(x + w - 40 * u, y + 29 * u),
                        Fade(IM_COL32(255, 255, 255, 40), a));
      y += head_h - 10 * u;
    }
    const bool sel = int(i) == row_;
    if (sel)
      dl->AddRectFilled(ImVec2(x + 24 * u, y + 2 * u), ImVec2(x + w - 24 * u, y + row_h - 2 * u),
                        Fade(IM_COL32(196, 22, 28, 50), a), 4 * u);
    dl->AddText(item, 28 * u, ImVec2(x + 40 * u, y + 5 * u), Fade(sel ? kWhite : kGrey, a), r.label);
    const std::string v = sel ? "<  " + ValueOf(r) + "  >" : ValueOf(r);
    const float vw = item->CalcTextSizeA(28 * u, FLT_MAX, 0, v.c_str()).x;
    dl->AddText(item, 28 * u, ImVec2(x + w - 40 * u - vw, y + 5 * u), Fade(sel ? kWhite : kGrey, a), v.c_str());
    y += row_h;
  }
  dl->AddText(body, 20 * u, ImVec2(x + 40 * u, y0 + h - 46 * u), Fade(kGrey, a),
              "UP DOWN  MOVE      LEFT RIGHT / X  CHANGE      O / L3+R3  CLOSE");
}

void StartQuickMenu(rex::ui::ImGuiDrawer* drawer) {
  if (g_menu || !drawer) return;
  LoadCheats();
  g_menu = std::make_unique<QuickMenu>(drawer);
}

}  // namespace mp3ps5

// The title's XInputGetState: r3 the user, r4 the state; returns 0 when read.
extern "C" void sub_8309DBD8(PPCContext& __restrict ctx, uint8_t* base) {
  const uint32_t user = ctx.r3.u32, state = ctx.r4.u32;
  __imp__sub_8309DBD8(ctx, base);
  if (user == 0 && state && ctx.r3.u32 == 0) mp3ps5::Filter(base, state);
}

#endif  // REX_PLATFORM_PS5
