// Max Payne 3 RePrescribed (PS5) - the menu (see mp3_menu.h): shown before
// the game is set up (Mp3App::OnFinalizePaths, asynchronous), driven by the
// DualSense through scePad, the menu song looping under it. L1/R1 change the
// page. (Its pages and trophy list are Crackdown PS5's crackdown_menu.cpp.)
#include "mp3_menu.h"
#if REX_PLATFORM_PS5

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cfloat>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <map>
#include <set>
#include <memory>
#include <string>
#include <vector>

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ui/imgui_dialog.h>
#include <rex/ui/imgui_drawer.h>
#include <rex/ui/immediate_drawer.h>

#include "game/sound.h"
#include "imgui.h"
#include "mp3_install.h"
#include "mp3_trophies.h"
#include "qrcodegen/qrcodegen.hpp"
#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#include "stb_image.h"

extern "C" {
int sceUserServiceInitialize(void* params);
int sceUserServiceGetInitialUser(int32_t* user_id);
int scePadInit(void);
int scePadOpen(int32_t user_id, int32_t type, int32_t index, const void* param);
int scePadGetHandle(int32_t user_id, int32_t type, int32_t index);
int scePadReadState(int32_t handle, void* data);
}

// The fonts, built into the app (gta4-ps5's ps5/assets/fonts: Oswald and
// Inter, SIL Open Font License).
#define MP3_INCBIN(name, file)                                                        \
  __asm__(".section .rodata\n.global " #name "\n.balign 16\n" #name ":\n.incbin \"" \
          file "\"\n.global " #name "_end\n" #name "_end:\n.byte 0\n.previous\n");    \
  extern "C" const unsigned char name[];                                              \
  extern "C" const unsigned char name##_end[];
MP3_INCBIN(mp3ps5_font_title, MP3PS5_FONTS_DIR "/oswald-latin-700-normal.ttf")
MP3_INCBIN(mp3ps5_font_item, MP3PS5_FONTS_DIR "/oswald-latin-600-normal.ttf")
MP3_INCBIN(mp3ps5_font_body, MP3PS5_FONTS_DIR "/inter-latin-400-normal.ttf")

namespace mp3ps5 {
namespace {

namespace fs = std::filesystem;

// (in the reports SEND LOGS makes)
constexpr const char* kAppVersion = "1.0.2";

ImFont* g_title = nullptr;
ImFont* g_item = nullptr;
ImFont* g_body = nullptr;

// The menu's pictures: kept for the app's life (the frame PLAY was pressed on
// is rendered after the dialog has gone).
std::vector<std::unique_ptr<rex::ui::ImmediateTexture>>* g_textures =
    new std::vector<std::unique_ptr<rex::ui::ImmediateTexture>>();

// ---- the pad ----

enum : uint32_t {
  kUp = 1, kDown = 2, kLeft = 4, kRight = 8, kCross = 16, kCircle = 32, kL1 = 64, kR1 = 128, kTriangle = 256,
  kSquare = 512, kOptions = 1024, kL2 = 2048, kR2 = 4096
};

constexpr uint32_t kPadUp = 0x00000010u, kPadRight = 0x00000020u, kPadDown = 0x00000040u,
                   kPadLeft = 0x00000080u, kPadL1 = 0x00000400u, kPadR1 = 0x00000800u,
                   kPadTriangle = 0x00001000u, kPadCircle = 0x00002000u, kPadCross = 0x00004000u,
                   kPadSquare = 0x00008000u, kPadOptions = 0x00000008u, kPadL2 = 0x00000100u,
                   kPadR2 = 0x00000200u;
struct PadData {
  uint32_t buttons;
  uint8_t lx, ly, rx, ry, l2, r2;
  uint16_t padding;
  uint8_t rest[240];
};

// (a handle of our own: the input driver later gets the same one)
class Pad {
 public:
  // The buttons pressed since the last read (held directions repeat).
  uint32_t Pressed() {
    if (handle_ < 0) {
      sceUserServiceInitialize(nullptr);
      scePadInit();
      int32_t user = -1;
      if (sceUserServiceGetInitialUser(&user) == 0 && user >= 0) {
        handle_ = scePadOpen(user, 0, 0, nullptr);
        if (handle_ < 0) handle_ = scePadGetHandle(user, 0, 0);
      }
      if (handle_ < 0) return 0;
    }
    PadData d{};
    if (scePadReadState(handle_, &d) != 0) return 0;
    uint32_t b = 0;
    if ((d.buttons & kPadUp) || d.ly < 64) b |= kUp;
    if ((d.buttons & kPadDown) || d.ly > 192) b |= kDown;
    if ((d.buttons & kPadLeft) || d.lx < 64) b |= kLeft;
    if ((d.buttons & kPadRight) || d.lx > 192) b |= kRight;
    if (d.buttons & kPadCross) b |= kCross;
    if (d.buttons & kPadCircle) b |= kCircle;
    if (d.buttons & kPadTriangle) b |= kTriangle;
    if (d.buttons & kPadL1) b |= kL1;
    if (d.buttons & kPadR1) b |= kR1;
    if (d.buttons & kPadSquare) b |= kSquare;
    if (d.buttons & kPadOptions) b |= kOptions;
    if (d.buttons & kPadL2) b |= kL2;
    if (d.buttons & kPadR2) b |= kR2;
    uint32_t pressed = b & ~last_;
    const auto now = std::chrono::steady_clock::now();
    constexpr uint32_t kDirs = kUp | kDown | kLeft | kRight;
    if (b & kDirs) {
      if ((b & kDirs) != (last_ & kDirs)) {
        repeat_at_ = now + std::chrono::milliseconds(350);
      } else if (now >= repeat_at_) {
        pressed |= b & kDirs;
        repeat_at_ = now + std::chrono::milliseconds(90);
      }
    }
    last_ = b;
    // (the button that started the app is still down on the first read)
    if (first_) {
      first_ = false;
      return 0;
    }
    return pressed;
  }

 private:
  int32_t handle_ = -1;
  uint32_t last_ = 0;
  bool first_ = true;
  std::chrono::steady_clock::time_point repeat_at_{};
};

// ---- the settings (app/menu.ini: key=value lines) ----

struct Settings {
  int disc = 1;  // 1 or 2
  int fps = 30;  // 30 (locked, the Xbox 360's) or 60
  bool hd_textures = true;
  bool fxaa = false;  // swap_post_effect: none or fxaa
  int filter = 3;     // anisotropic_override 2..5: 2x, 4x, 8x, 16x
  bool lane = true;   // the GPU's second lane (crackdown_gpu_lane)
  int music = 60;     // the menu song's level, 0..100
  int language = 1;   // the Xbox 360's language ID (user_language): kLanguages

  static constexpr const char* kFilterNames[] = {"", "", "2X", "4X", "8X", "16X"};
  // The disc's languages (the World release: En, Fr, De, Es, It, Ru), by their Xbox 360 IDs.
  struct Language {
    int id;
    const char* name;
  };
  static constexpr Language kLanguages[] = {
      {1, "ENGLISH"}, {4, "FRENCH"}, {3, "GERMAN"}, {5, "SPANISH"}, {6, "ITALIAN"},
      {9, "PORTUGUESE (BR)"}, {11, "POLISH"}, {12, "RUSSIAN"}};  // (Portuguese and Polish: Title Update 9's)
  static int LanguageIndex(int id) {
    for (int i = 0; i < int(std::size(kLanguages)); ++i)
      if (kLanguages[i].id == id) return i;
    return 0;
  }

  static Settings Load(const fs::path& app) {
    Settings s;
    if (std::FILE* f = std::fopen((app / "menu.ini").string().c_str(), "rb")) {
      char line[256];
      while (std::fgets(line, sizeof(line), f)) {
        char key[64] = {};
        int value = 0;
        if (std::sscanf(line, " %63[a-z_] = %d", key, &value) != 2) continue;
        const std::string k(key);
        if (k == "frame_rate") s.fps = value == 60 ? 60 : 30;
        if (k == "frame_rate_unlocked") s.fps = value ? 60 : 30;  // (the earlier menu's)
        if (k == "hd_textures") s.hd_textures = value != 0;
        if (k == "fxaa") s.fxaa = value != 0;
        if (k == "texture_filtering") s.filter = std::clamp(value, 2, 5);
        if (k == "gpu_lane") s.lane = value != 0;
        if (k == "menu_music") s.music = std::clamp(value, 0, 100);
        if (k == "language") s.language = kLanguages[LanguageIndex(value)].id;
      }
      std::fclose(f);
    }
    // the disc: disc.txt (Mp3App's, also written when the game asks for the other disc)
    if (std::FILE* f = std::fopen((app / "disc.txt").string().c_str(), "rb")) {
      if (std::fgetc(f) == '2') s.disc = 2;
      std::fclose(f);
    }
    return s;
  }

  void Save(const fs::path& app) const {
    if (std::FILE* f = std::fopen((app / "menu.ini").string().c_str(), "wb")) {
      std::fprintf(f,
                   "frame_rate = %d\nhd_textures = %d\nfxaa = %d\ntexture_filtering = %d\ngpu_lane = %d\nmenu_music = %d\n"
                   "language = %d\n",
                   fps, hd_textures ? 1 : 0, fxaa ? 1 : 0, filter, lane ? 1 : 0, music, language);
      std::fclose(f);
    }
    if (std::FILE* f = std::fopen((app / "disc.txt").string().c_str(), "wb")) {
      std::fputc(disc == 2 ? '2' : '1', f);
      std::fclose(f);
    }
  }

  // The cvars they set, for this start.
  void Apply() const {
    // (the Xbox 360's own resolution, always: 2x cost the PS5 far more than it showed)
    rex::cvar::SetFlagByName("draw_resolution_scale_x", "1");
    rex::cvar::SetFlagByName("draw_resolution_scale_y", "1");
    rex::cvar::SetFlagByName("hd_textures", hd_textures ? "true" : "false");
    // The game's frame lock (mp3_app.cpp): 2 vblanks a frame, as on the Xbox
    // 360, or 1. The guest's vblanks follow the TV's 60 Hz either way.
    rex::cvar::SetFlagByName("mp3_frame_lock", fps == 30 ? "2" : "1");
    rex::cvar::SetFlagByName("vsync", "true");
    rex::cvar::SetFlagByName("swap_post_effect", fxaa ? "fxaa" : "none");
    rex::cvar::SetFlagByName("anisotropic_override", std::to_string(filter));
    rex::cvar::SetFlagByName("crackdown_gpu_lane", lane ? "true" : "false");
    // (the game's text, menus and subtitles follow XGetLanguage: ci/patch_framework.py)
    rex::cvar::SetFlagByName("user_language", std::to_string(language));
    REXLOG_INFO("Max Payne 3 menu: disc {}, frame rate {}, HD textures {}, anti-aliasing {}, texture filtering {}, "
                "the GPU's second lane {}, language {}",
                disc, fps == 30 ? "30 (locked)" : "60", hd_textures ? "on" : "off", fxaa ? "FXAA" : "off",
                kFilterNames[filter], lane ? "on" : "off", kLanguages[LanguageIndex(language)].name);
  }
};

// ---- the look ----

constexpr ImU32 kWhite = IM_COL32(236, 232, 226, 255);
constexpr ImU32 kGrey = IM_COL32(150, 146, 140, 255);
constexpr ImU32 kDim = IM_COL32(90, 88, 84, 255);
constexpr ImU32 kRed = IM_COL32(196, 22, 28, 255);
constexpr ImU32 kText = IM_COL32(205, 202, 196, 255);

struct Image {
  ImTextureID id = 0;
  float w = 0, h = 0;
  explicit operator bool() const { return id != 0; }
};

float Width(ImFont* f, float size, const std::string& s) { return f->CalcTextSizeA(size, FLT_MAX, 0, s.c_str()).x; }

// `s` cut to `width` (with "..."), in whole UTF-8 letters.
std::string Fit(ImFont* f, float size, std::string s, float width) {
  if (Width(f, size, s) <= width) return s;
  while (!s.empty() && Width(f, size, s + "...") > width) {
    s.pop_back();
    while (!s.empty() && (static_cast<unsigned char>(s.back()) & 0xC0) == 0x80) s.pop_back();
  }
  return s + "...";
}

std::string Date(int64_t t, const char* format) {
  const std::time_t tt = std::time_t(t);
  char out[64] = {};
  if (const std::tm* tm = std::localtime(&tt)) std::strftime(out, sizeof(out), format, tm);
  return out;
}

std::string Upper(std::string s) {
  for (char& c : s) c = char(std::toupper(static_cast<unsigned char>(c)));
  return s;
}

// Letter-spaced text; returns its width.
float Spaced(ImDrawList* dl, ImFont* f, float size, ImVec2 p, ImU32 col, const char* t, float spacing) {
  const float x0 = p.x;
  for (const char* c = t; *c; ++c) {
    dl->AddText(f, size, p, col, c, c + 1);
    p.x += f->CalcTextSizeA(size, FLT_MAX, 0, c, c + 1).x + spacing;
  }
  return p.x - x0 - spacing;
}

// The pages, in the tab bar's order.
enum Page { kHome, kContent, kSaves, kSettings, kTrophies, kCredits, kPages };
const char* const kPageNames[kPages] = {"HOME", "CONTENT", "SAVES", "SETTINGS", "TROPHIES", "CREDITS"};

// The settings page's rows, in its order (a new option: a row here, its line
// in SettingsLines and its change in ChangeSetting).
// The saves page's rows: its two actions, then the saves and backups in install/.
enum SavesRow { kBackupRow, kSendLogsRow, kSavesFirst };

enum SettingsRow { kDisc, kLanguage, kFrameRate, kTextures, kAntiAliasing, kFiltering, kLane, kMusic, kSettingsRows };

class MenuDialog final : public rex::ui::ImGuiDialog {
 public:
  MenuDialog(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate, const fs::path& app,
             std::function<void(MenuChoice)> play)
      : ImGuiDialog(drawer), immediate_(immediate), app_(app), play_(std::move(play)),
        settings_(Settings::Load(app)) {
    const fs::path hub = app / "hub";
    for (const char* name : {"background.jpg", "background.png"})
      if (!background_) background_ = Load(hub / name);
    logo_ = Load(hub / "logo.png");
    std::error_code ec;
    // (an install the app was closed during: not to be taken for a finished one)
    fs::remove_all(app / "game.installing", ec);
    fs::remove_all(app / "update.installing", ec);
    for (fs::directory_iterator it(app / "dlc", ec), end; !ec && it != end; it.increment(ec))
      if (it->path().extension() == ".installing") fs::remove_all(it->path(), ec);
    ec.clear();
    Refresh();
    REXLOG_INFO("Max Payne 3 menu: disc 1 {}, disc 2 {}, Title Update 9 {}/{}, {} DLC packs, {} in install/",
                installed_.disc[0] ? "installed" : "MISSING", installed_.disc[1] ? "installed" : "missing",
                installed_.update[0] ? "yes" : "NO", installed_.update[1] ? "yes" : "no", installed_.dlc,
                sources_.size());
    has_disc2_ = fs::exists(app / "game2" / "default.xex", ec);
    has_textures_ = fs::is_directory(app / "textures", ec) && !fs::is_empty(app / "textures", ec);
    if (!has_disc2_) settings_.disc = 1;
    StartMusic(hub / "music");
    // (the runtime's user data: /app0/UserData/mp3)
    user_data_ = app / "UserData" / "mp3";
    TakeTrophies(trophies::Load(user_data_));
    REXLOG_INFO("Max Payne 3 menu: trophies {} ({} of {} earned)", trophies_.loaded ? "listed" : "not listed yet",
                trophies_.earned, trophies_.earnable);
  }

 protected:
  void OnDraw(ImGuiIO& io) override {
    if (done_) return;
    const float dt = std::min(io.DeltaTime, 0.1f);
    time_ += dt;
    pictures_this_frame_ = 0;
    HandlePad(pad_.Pressed());
    TickInstaller();
    if (music_) music_->SetVolume(leaving_ ? 0.0f : settings_.music / 100.0f);
    tab_x_ += (tab_target_x_ - tab_x_) * std::min(1.0f, dt * 14.0f);
    tab_w_ += (tab_target_w_ - tab_w_) * std::min(1.0f, dt * 14.0f);
    page_in_ = std::min(1.0f, page_in_ + dt * 5.0f);
    Draw(io.DisplaySize, dt);
    if (leaving_ && (leave_t_ += dt) > 0.7f) {
      done_ = true;
      settings_.Save(app_);
      settings_.Apply();
      Close();
      play_(MenuChoice{uint32_t(settings_.disc)});
    }
  }

 private:
  // ---- the installer (mp3_install.h) ----

  void Refresh() {
    installed_ = install::Check(app_);
    sources_ = install::Scan(app_);
    std::error_code ec;
    has_disc2_ = fs::exists(app_ / "game2" / "default.xex", ec);
    has_textures_ = installed_.textures;
  }

  // The content rows: disc 1, disc 2, Title Update 9, the DLC.
  enum ContentRow { kRowDisc1, kRowDisc2, kRowUpdate, kRowDlc, kRowTextures, kContentRows };

  // What waits in install/ for a content row; nullptr: nothing. (A disc image tells which disc it
  // is when it's read: it's offered on the first disc not installed.)
  const install::Source* SourceFor(int row) const {
    const int free_disc = !installed_.disc[0] ? 1 : !installed_.disc[1] ? 2 : 1;
    for (const auto& s : sources_) {
      const bool game = s.kind == install::Kind::kGameImage || s.kind == install::Kind::kGameFolder;
      if (row <= kRowDisc2 && game && (s.disc ? s.disc : free_disc) == row + 1) return &s;
      if (row == kRowUpdate && (s.kind == install::Kind::kUpdate || s.kind == install::Kind::kOtherUpdate) &&
          !s.installed)
        return &s;
      if (row == kRowDlc && s.kind == install::Kind::kDlc && !s.installed) return &s;
      if (row == kRowTextures && s.kind == install::Kind::kTexturePack) return &s;
    }
    return nullptr;
  }
  int DlcWaiting() const {
    int n = 0;
    for (const auto& s : sources_) n += s.kind == install::Kind::kDlc && !s.installed;
    return n;
  }
  bool RowInstalled(int row) const {
    switch (row) {
      case kRowDisc1: return installed_.disc[0];
      case kRowDisc2: return installed_.disc[1];
      case kRowUpdate: return installed_.update[0] && installed_.update[1];
      case kRowDlc: return installed_.dlc > 0;
      default: return installed_.textures;
    }
  }
  // The chosen disc and its title update: what PLAY needs.
  bool Ready() const {
    const int d = settings_.disc == 2 ? 1 : 0;
    return installed_.disc[d] && installed_.update[d];
  }
  // The saves page's rows after BACK UP: the saves and backups to import or restore.
  std::vector<const install::Source*> SaveSources() const {
    std::vector<const install::Source*> out;
    for (const auto& s : sources_)
      if (s.kind == install::Kind::kSave || s.kind == install::Kind::kBackup) out.push_back(&s);
    return out;
  }

  // Once a frame: a finished job's outcome, and the next of what installs by itself (the discs,
  // Title Update 9, the DLC), one at a time.
  void TickInstaller() {
    auto& job = install::TheJob();
    if (job.Generation() != job_generation_) {
      job_generation_ = job.Generation();
      result_t_ = time_;
      Refresh();
    }
    // (not in the first seconds: the menu on screen first, the progress with it)
    if (job.Busy() || leaving_ || time_ < 2.0f) return;
    bool started = false;
    for (const auto& s : sources_) {
      // (one installed already, in an earlier session, or an avatar item: only to delete or keep)
      if (s.installed || s.kind == install::Kind::kAvatarItem) {
        if (!tried_.count(s.path.string())) tried_.insert(s.path.string()), job.Leftover(s.path);
        continue;
      }
      if (!install::Automatic(s.kind) || tried_.count(s.path.string())) continue;
      if (s.kind == install::Kind::kGameFolder && s.disc && installed_.disc[s.disc - 1]) continue;
      if (s.kind == install::Kind::kGameImage && installed_.disc[0] && installed_.disc[1]) continue;
      if (s.kind == install::Kind::kUpdate && RowInstalled(kRowUpdate)) continue;
      if (s.kind == install::Kind::kTexturePack && installed_.textures) continue;
      tried_.insert(s.path.string());
      job.Install(app_, s);
      started = true;
      break;
    }
    // all done: delete or keep what they were installed from (after the last outcome has been read)
    if (!started && !ask_leftovers_ && !job.Leftovers().empty() && time_ - result_t_ > 2.5f) {
      ask_leftovers_ = true;
      leftover_bytes_ = job.LeftoverBytes();
    }
  }

  // A second press of the same button on the same row within 3 s: the go-ahead.
  bool Confirmed(int key) {
    if (confirm_key_ == key && time_ - confirm_t_ < 3.0f) {
      confirm_key_ = -1;
      return true;
    }
    confirm_key_ = key;
    confirm_t_ = time_;
    return false;
  }
  bool Confirming(int key) const { return confirm_key_ == key && time_ - confirm_t_ < 3.0f; }

  // ---- input ----

  void HandlePad(uint32_t p) {
    if (leaving_ || !p) return;
    if (ask_leftovers_) {
      // (the question has the pad to itself)
      if (p & kCross) install::TheJob().DeleteLeftovers(app_), ask_leftovers_ = false;
      if (p & kCircle) install::TheJob().KeepLeftovers(app_), ask_leftovers_ = false, Refresh();
      return;
    }
    if (reporting_) {
      HandleReport(p);  // (the keyboard has the pad to itself)
      return;
    }
    if (p & (kL1 | kR1)) {
      page_ = Page((page_ + ((p & kR1) ? 1 : kPages - 1)) % kPages);
      page_in_ = 0;
      return;
    }
    if ((p & kCircle) && page_ != kHome) {
      page_ = kHome;
      page_in_ = 0;
      return;
    }
    switch (page_) {
      case kHome:
        if ((p & kCross) && install::TheJob().Busy()) break;  // (PLAY waits for the install)
        if ((p & kCross) && !Ready()) {
          Refresh();  // (copied in since the menu opened?)
          missing_flash_ = 1.0f;
        }
        if ((p & kCross) && Ready()) {
          leaving_ = true;
          if (music_) music_->FadeOut(0.6f);
        }
        break;
      case kContent: {
        if (p & kUp) content_row_ = (content_row_ + kContentRows - 1) % kContentRows;
        if (p & kDown) content_row_ = (content_row_ + 1) % kContentRows;
        auto& job = install::TheJob();
        if (job.Busy()) break;
        if (p & kCross) {
          Refresh();
          if (const install::Source* s = SourceFor(content_row_)) job.Install(app_, *s);
        }
        if ((p & kTriangle) && RowInstalled(content_row_) && Confirmed(100 + content_row_))
          job.Uninstall(app_, install::Remove(content_row_));
        break;
      }
      case kSaves: {
        const int rows = kSavesFirst + int(SaveSources().size());
        if (p & kUp) saves_row_ = (saves_row_ + rows - 1) % rows;
        if (p & kDown) saves_row_ = (saves_row_ + 1) % rows;
        saves_row_ = std::min(saves_row_, rows - 1);
        auto& job = install::TheJob();
        if (job.Busy() || !(p & kCross)) break;
        if (saves_row_ == kBackupRow) {
          job.Backup(app_);
        } else if (saves_row_ == kSendLogsRow) {
          reporting_ = true;  // (what went wrong typed first: HandleReport)
        } else if (Confirmed(200 + saves_row_)) {
          job.Install(app_, *SaveSources()[size_t(saves_row_ - kSavesFirst)]);
        }
        break;
      }
      case kSettings: {
        if (p & kUp) row_ = (row_ + kSettingsRows - 1) % kSettingsRows;
        if (p & kDown) row_ = (row_ + 1) % kSettingsRows;
        const int step = (p & kRight) ? 1 : (p & kLeft) ? -1 : 0;
        ChangeSetting(row_, step, (p & kCross) != 0);
        break;
      }
      case kTrophies: {
        const int n = int(items_.size());
        if (!n) break;
        if (p & kUp) trophy_ = (trophy_ + n - 1) % n;
        if (p & kDown) trophy_ = (trophy_ + 1) % n;
        if (p & kLeft) trophy_ = std::max(0, trophy_ - 8);
        if (p & kRight) trophy_ = std::min(n - 1, trophy_ + 8);
        if (p & (kUp | kDown | kLeft | kRight)) detail_in_ = 0;
        break;
      }
      default:
        break;
    }
  }

  // ---- the settings ----

  struct SettingLine {
    const char* section;  // the heading it's under
    const char* label;
    std::string value;
    bool enabled;
    const char* help;
    bool slider = false;  // (a level: the bar, not < >)
  };

  std::vector<SettingLine> SettingsLines() const {
    char music[16];
    std::snprintf(music, sizeof(music), "%d%%", settings_.music);
    std::vector<SettingLine> lines(kSettingsRows);
    lines[kDisc] = {"GAME", "DISC", settings_.disc == 2 ? "2 - SECOND HALF" : "1 - FIRST HALF", has_disc2_,
                    has_disc2_ ? "Which of the Xbox 360's two discs the game starts on. The game asks for disc 2 on "
                                 "its own when the story gets there, and the app starts again on it."
                               : "Disc 2's files aren't in the app's game2/ folder: the game runs from disc 1."};
    lines[kLanguage] = {"GAME", "LANGUAGE", Settings::kLanguages[Settings::LanguageIndex(settings_.language)].name,
                        true,
                        "The language of the game's text, menus and subtitles: English, French, German, Spanish, "
                        "Italian, Brazilian Portuguese, Polish or Russian. The voices stay in English, as on the Xbox 360."};
    lines[kFrameRate] = {"GRAPHICS", "FRAME RATE", settings_.fps == 30 ? "30 (LOCKED)" : "60", true,
                         "30 locks the game to the Xbox 360's own rate: steady, as Max Payne 3 was made. 60 lets it "
                         "run faster, up to the TV's 60 frames a second, when the PS5 keeps up; the busiest fights "
                         "may dip below it."};
    lines[kTextures] = {"GRAPHICS", "HD TEXTURES", has_textures_ ? (settings_.hd_textures ? "ON" : "OFF") : "NOT INSTALLED",
                        has_textures_,
                        has_textures_ ? "The high resolution textures in the app's textures/ folder, in place of the "
                                        "Xbox 360's."
                                      : "Put a converted texture pack in the app's textures/ folder to use it here."};
    lines[kAntiAliasing] = {"GRAPHICS", "ANTI-ALIASING", settings_.fxaa ? "FXAA" : "OFF", true,
                            "FXAA smooths the jagged edges of the picture as it goes to the screen. It costs the PS5's "
                            "GPU a little and blurs fine detail slightly."};
    lines[kFiltering] = {"GRAPHICS", "TEXTURE FILTERING",
                         std::string(Settings::kFilterNames[settings_.filter]) + " ANISOTROPIC", true,
                         "How sharp floors, walls and streets stay when seen at an angle. Higher is sharper; the PS5's "
                         "GPU handles 16X easily."};
    lines[kLane] = {"GRAPHICS", "GPU HELPER", settings_.lane ? "ON" : "OFF", true,
                    "A second thread that takes part of the graphics work - copying the game's memory for the GPU and "
                    "writing its texture lists - off the thread that sets the frame rate. Turn it off if something "
                    "draws wrong."};
    lines[kMusic] = {"AUDIO", "MENU MUSIC", music, true, "How loud this menu's song is.", true};
    return lines;
  }

  void ChangeSetting(int row, int step, bool cross) {
    const bool flip = step || cross;
    switch (row) {
      case kDisc:
        if (flip && has_disc2_) settings_.disc = settings_.disc == 1 ? 2 : 1;
        break;
      case kLanguage: {
        const int n = int(std::size(Settings::kLanguages));
        const int i = (Settings::LanguageIndex(settings_.language) + (step ? step : cross ? 1 : 0) + n) % n;
        settings_.language = Settings::kLanguages[i].id;
        break;
      }
      case kFrameRate:
        if (flip) settings_.fps = settings_.fps == 30 ? 60 : 30;
        break;
      case kTextures:
        if (flip && has_textures_) settings_.hd_textures = !settings_.hd_textures;
        break;
      case kAntiAliasing:
        if (flip) settings_.fxaa = !settings_.fxaa;
        break;
      case kFiltering:
        if (cross) settings_.filter = settings_.filter == 5 ? 2 : settings_.filter + 1;
        settings_.filter = std::clamp(settings_.filter + step, 2, 5);
        break;
      case kLane:
        if (flip) settings_.lane = !settings_.lane;
        break;
      case kMusic:
        settings_.music = std::clamp(settings_.music + step * 10, 0, 100);
        break;
    }
  }

  // ---- the trophies ----

  // The list's groups.
  enum Group { kEarned, kNotYet, kMultiplayer };

  // A row of the list: a trophy (its index; -1: the platinum), in its group.
  struct Item {
    int index = 0, group = 0;
  };

  void TakeTrophies(trophies::List list) {
    trophies_ = std::move(list);
    items_.clear();
    if (!trophies_.loaded) return;
    const auto& all = trophies_.trophies;
    std::vector<Item> earned, rest, online;
    for (int i = 0; i < int(all.size()); ++i) {
      const auto& t = all[size_t(i)];
      if (t.online()) online.push_back({i, kMultiplayer});
      else (t.earned ? earned : rest).push_back({i, t.earned ? kEarned : kNotYet});
    }
    std::stable_sort(earned.begin(), earned.end(), [&](const Item& a, const Item& b) {
      return all[size_t(a.index)].earned > all[size_t(b.index)].earned;
    });
    const bool plat = trophies_.Platinum();
    if (plat) items_.push_back({-1, kEarned});
    items_.insert(items_.end(), earned.begin(), earned.end());
    if (!plat) items_.push_back({-1, kNotYet});
    items_.insert(items_.end(), rest.begin(), rest.end());
    items_.insert(items_.end(), online.begin(), online.end());
  }

  bool Earned(const Item& it) const {
    return it.index < 0 ? trophies_.Platinum() : trophies_.trophies[size_t(it.index)].earned != 0;
  }
  trophies::Grade GradeOf(const Item& it) const {
    return it.index < 0 ? trophies::Grade::kPlatinum : trophies_.trophies[size_t(it.index)].grade();
  }
  std::string NameOf(const Item& it) const {
    if (it.index < 0) return "Max Payne 3 RePrescribed Platinum";
    const auto& t = trophies_.trophies[size_t(it.index)];
    return (!t.earned && t.hidden()) ? "Hidden trophy" : t.name;
  }
  std::string TextOf(const Item& it) const {
    if (it.index < 0)
      return "Earn every other Max Payne 3 trophy (" + std::to_string(trophies_.earnable) +
             ", the multiplayer ones aside).";
    const auto& t = trophies_.trophies[size_t(it.index)];
    std::string text;
    if (!t.earned && t.hidden()) text = "Keep playing to find out what this one is.";
    else text = t.earned || t.locked_description.empty() ? t.description : t.locked_description;
    if (t.online())
      text += "\n\nMultiplayer: it needs Rockstar's online service, which this port can't reach, so it doesn't "
              "count toward the platinum.";
    return text;
  }

  // A trophy's picture (the platinum's: the game's own), loaded the first time it's drawn
  // (two a frame at most, so the page doesn't hold the menu up).
  Image Picture(const Item& it) {
    const uint32_t key = it.index < 0 ? 0 : std::max(1u, trophies_.trophies[size_t(it.index)].image_id);
    if (auto found = pictures_.find(key); found != pictures_.end()) return found->second;
    if (pictures_this_frame_ >= 2) return {};
    ++pictures_this_frame_;
    const Image img = Load(it.index < 0 ? trophies::TitleIconFile(user_data_)
                                        : trophies::IconFile(user_data_, trophies_.trophies[size_t(it.index)].image_id));
    pictures_[key] = img;
    return img;
  }

  // The picture in a square: dimmed until it's earned, with a padlock; a question mark for a hidden one.
  void Icon(ImDrawList* dl, const Item& it, float x, float y, float size, float a) {
    const bool earned = Earned(it);
    const trophies::Trophy* t = it.index < 0 ? nullptr : &trophies_.trophies[size_t(it.index)];
    const float r = size * 0.08f;
    if (t && !earned && t->hidden()) {
      dl->AddRectFilled(ImVec2(x, y), ImVec2(x + size, y + size), IM_COL32(38, 36, 36, int(255 * a)), r);
      const char* q = "?";
      const float qs = size * 0.6f;
      dl->AddText(g_item, qs, ImVec2(x + (size - Width(g_item, qs, q)) * 0.5f, y + size * 0.16f),
                  IM_COL32(255, 255, 255, int(128 * a)), q);
      return;
    }
    const Image img = Picture(it);
    if (img) {
      const int tint = earned ? 255 : 90;
      dl->AddImageRounded(img.id, ImVec2(x, y), ImVec2(x + size, y + size), ImVec2(0, 0), ImVec2(1, 1),
                          IM_COL32(tint, tint, tint, int(255 * a)), r);
    } else {
      dl->AddRectFilled(ImVec2(x, y), ImVec2(x + size, y + size), IM_COL32(38, 36, 36, int(255 * a)), r);
      trophies::DrawCup(dl, ImVec2(x + size * 0.5f, y + size * 0.5f), size * 0.56f, GradeOf(it), a, earned);
    }
    if (!earned) {  // a padlock in the corner
      const float k = size / 64.0f, lx = x + size - 12 * k, ly = y + size - 12 * k;
      dl->AddCircleFilled(ImVec2(lx, ly), 12 * k, IM_COL32(0, 0, 0, int(190 * a)), 20);
      dl->AddRectFilled(ImVec2(lx - 5 * k, ly - 1.5f * k), ImVec2(lx + 5 * k, ly + 6 * k),
                        IM_COL32(230, 230, 230, int(220 * a)), 1.5f * k);
      dl->PathClear();
      dl->PathArcTo(ImVec2(lx, ly - 2 * k), 3.6f * k, 3.14159f, 6.28318f, 12);
      dl->PathStroke(IM_COL32(230, 230, 230, int(220 * a)), 0, 1.8f * k);
    }
  }

  // ---- drawing ----

  void Draw(const ImVec2& size, float dt) {
    ImDrawList* dl = ImGui::GetForegroundDrawList();
    const float u = size.y / 1080.0f;  // (laid out for 1080p, scaled to the screen)
    const float fade = leaving_ ? std::clamp(1.0f - leave_t_ / 0.6f, 0.0f, 1.0f) : std::min(time_ / 0.8f, 1.0f);
    fade_ = fade;
    u_ = u;

    DrawBackground(dl, size, dt);
    DrawTabs(dl, size);
    const float in = 1 - (1 - page_in_) * (1 - page_in_);
    page_alpha_ = fade * in;
    page_dx_ = (1 - in) * 24 * u;
    switch (page_) {
      case kHome: DrawHome(dl, size); break;
      case kContent: DrawContent(dl, size); break;
      case kSaves: DrawSaves(dl, size); break;
      case kSettings: DrawSettings(dl, size); break;
      case kTrophies: DrawTrophies(dl, size, dt); break;
      default: DrawCredits(dl, size); break;
    }
    DrawFooter(dl, size);
    if (ask_leftovers_) DrawLeftoversQuestion(dl, size);
    if (reporting_) DrawReport(dl, size);
  }

  // ---- SEND LOGS: what went wrong, typed with the pad, and the GitHub issue's QR code ----

  // The keyboard: four rows of keys, then SPACE, DELETE and SEND.
  static constexpr const char* kKeys[4] = {"1234567890", "QWERTYUIOP", "ASDFGHJKL'", "ZXCVBNM,.?"};
  static constexpr int kKeyRows = 5, kKeyCols = 10;
  static constexpr size_t kNoteMax = 300;  // (the QR code stays easy to scan off a TV)
  // the bottom row's three keys, by column
  static int BottomKey(int col) { return col < 5 ? 0 : col < 8 ? 1 : 2; }  // SPACE, DELETE, SEND

  void TypeKey(char c) {
    if (note_.size() >= kNoteMax) return;
    note_ += (c >= 'A' && c <= 'Z' && !caps_) ? char(c - 'A' + 'a') : c;
  }

  void HandleReport(uint32_t p) {
    if (p & kCircle) {
      reporting_ = false;
      return;
    }
    if (p & kUp) key_row_ = (key_row_ + kKeyRows - 1) % kKeyRows;
    if (p & kDown) key_row_ = (key_row_ + 1) % kKeyRows;
    if (p & kLeft) key_col_ = (key_col_ + kKeyCols - 1) % kKeyCols;
    if (p & kRight) key_col_ = (key_col_ + 1) % kKeyCols;
    if (p & kL1) caps_ = !caps_;
    if (p & kSquare) {
      if (!note_.empty()) note_.pop_back();
    }
    if (p & kTriangle) TypeKey(' ');
    bool send = (p & (kOptions | kR2)) != 0;
    if (p & kCross) {
      if (key_row_ < 4) {
        TypeKey(kKeys[key_row_][key_col_]);
      } else {
        switch (BottomKey(key_col_)) {
          case 0: TypeKey(' '); break;
          case 1: if (!note_.empty()) note_.pop_back(); break;
          default: send = true; break;
        }
      }
    }
    if (send && !install::TheJob().Busy()) {
      sent_note_ = note_;
      sent_settings_ = SettingsSummary();
      reporting_ = false;
      install::TheJob().SendLogs(app_, note_);
    }
  }

  // The settings, for the report ("60 FPS, HD textures on, ...").
  std::string SettingsSummary() const {
    std::string s = settings_.fps == 30 ? "30 FPS (locked)" : "60 FPS";
    s += std::string(", HD textures ") + (has_textures_ ? (settings_.hd_textures ? "on" : "off") : "not installed");
    s += std::string(", anti-aliasing ") + (settings_.fxaa ? "FXAA" : "off");
    s += std::string(", filtering ") + Settings::kFilterNames[settings_.filter];
    s += std::string(", GPU helper ") + (settings_.lane ? "on" : "off");
    s += std::string(", language ") + Settings::kLanguages[Settings::LanguageIndex(settings_.language)].name;
    s += std::string(", disc ") + (settings_.disc == 2 ? "2" : "1");
    return s;
  }

  static std::string UrlEncode(const std::string& in) {
    static const char* hex = "0123456789ABCDEF";
    std::string out;
    for (unsigned char c : in) {
      if (std::isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
        out += char(c);
      } else {
        out += '%';
        out += hex[c >> 4];
        out += hex[c & 15];
      }
    }
    return out;
  }

  // A new issue on the project's GitHub, filled in with the report, the logs' link and the settings.
  std::string IssueUrl(const std::string& link) const {
    std::string title = "Report from the PS5";
    if (!sent_note_.empty()) title += ": " + sent_note_.substr(0, 60) + (sent_note_.size() > 60 ? "..." : "");
    std::string body = "**What happened**\n" + (sent_note_.empty() ? std::string("(not typed in)") : sent_note_) +
                       "\n\n**Logs:** https://" + link + "\n**Version:** " + kAppVersion +
                       "\n**Settings:** " + sent_settings_ + "\n\n(Sent from the app's SAVES > SEND LOGS.)\n";
    return std::string("https://github.com/ChaosTheory1075/mp3-ps5/issues/new?title=") + UrlEncode(title) +
           "&body=" + UrlEncode(body);
  }

  // The QR code for the latest link (made once per link).
  void UpdateQr(const std::string& link) {
    if (link == qr_link_) return;
    qr_link_ = link;
    qr_.clear();
    qr_size_ = 0;
    if (link.empty()) return;
    try {
      const auto qr = qrcodegen::QrCode::encodeText(IssueUrl(link).c_str(), qrcodegen::QrCode::Ecc::LOW);
      qr_size_ = qr.getSize();
      qr_.resize(size_t(qr_size_) * size_t(qr_size_));
      for (int y = 0; y < qr_size_; ++y)
        for (int x = 0; x < qr_size_; ++x) qr_[size_t(y) * size_t(qr_size_) + size_t(x)] = qr.getModule(x, y);
    } catch (const std::exception& e) {
      REXLOG_WARN("Max Payne 3 menu: no QR code for the report ({})", e.what());
      qr_.clear();
      qr_size_ = 0;
    }
  }

  // The QR code, `side` wide with its white margin, at (x, y).
  void DrawQr(ImDrawList* dl, float x, float y, float side) {
    if (!qr_size_) return;
    const int border = 3;
    const float m = std::max(1.0f, std::floor(side / float(qr_size_ + border * 2)));
    const float full = m * float(qr_size_ + border * 2);
    dl->AddRectFilled(ImVec2(x, y), ImVec2(x + full, y + full), P(IM_COL32(255, 255, 255, 255)));
    for (int row = 0; row < qr_size_; ++row) {
      for (int col = 0; col < qr_size_; ++col) {
        if (!qr_[size_t(row) * size_t(qr_size_) + size_t(col)]) continue;
        const float px = x + float(col + border) * m, py = y + float(row + border) * m;
        dl->AddRectFilled(ImVec2(px, py), ImVec2(px + m, py + m), P(IM_COL32(0, 0, 0, 255)));
      }
    }
  }

  // "What went wrong?": the text so far and the keyboard, over the page.
  void DrawReport(ImDrawList* dl, const ImVec2& size) {
    const float u = u_;
    dl->AddRectFilled(ImVec2(0, 0), size, A(IM_COL32(0, 0, 0, 190)));
    const float w = 1300 * u, h = 860 * u, x = (size.x - w) * 0.5f, y = (size.y - h) * 0.5f;
    dl->AddRectFilled(ImVec2(x, y), ImVec2(x + w, y + h), A(IM_COL32(14, 12, 12, 250)), 10 * u);
    dl->AddRectFilled(ImVec2(x, y), ImVec2(x + 8 * u, y + h), A(kRed));
    const float l = x + 50 * u;
    dl->AddText(g_item, 44 * u, ImVec2(l, y + 36 * u), A(kWhite), "SEND LOGS");
    dl->AddText(g_body, 26 * u, ImVec2(l, y + 96 * u), A(kText),
                "Say what went wrong (or leave it empty), then SEND. The logs go to catbox.moe, and a QR code "
                "opens a GitHub issue with your words and the logs' link filled in. Anyone with the link can read "
                "the logs.",
                nullptr, w - 100 * u);
    // the text so far, with a cursor
    const float ty = y + 200 * u, tw = w - 100 * u;
    dl->AddRectFilled(ImVec2(l, ty), ImVec2(l + tw, ty + 150 * u), A(IM_COL32(30, 28, 28, 255)), 6 * u);
    std::string shown = note_ + ((int(time_ * 2) % 2) ? "_" : " ");
    dl->AddText(g_body, 28 * u, ImVec2(l + 16 * u, ty + 12 * u), A(kWhite), shown.c_str(), nullptr, tw - 32 * u);
    char count[32];
    std::snprintf(count, sizeof(count), "%zu / %zu", note_.size(), kNoteMax);
    dl->AddText(g_body, 20 * u, ImVec2(l + tw - Width(g_body, 20 * u, count), ty + 158 * u), A(kDim), count);
    // the keys
    const float ky = ty + 200 * u, kw = tw / kKeyCols, kh = 64 * u;
    for (int r = 0; r < 4; ++r) {
      for (int c = 0; c < kKeyCols; ++c) {
        const bool sel = r == key_row_ && c == key_col_;
        const float kx = l + c * kw, yy = ky + r * (kh + 8 * u);
        dl->AddRectFilled(ImVec2(kx + 3 * u, yy), ImVec2(kx + kw - 3 * u, yy + kh),
                          A(sel ? kRed : IM_COL32(40, 38, 38, 255)), 5 * u);
        char key[2] = {kKeys[r][c], 0};
        if (key[0] >= 'A' && key[0] <= 'Z' && !caps_) key[0] = char(key[0] - 'A' + 'a');
        dl->AddText(g_body, 32 * u, ImVec2(kx + (kw - Width(g_body, 32 * u, key)) * 0.5f, yy + 14 * u),
                    A(kWhite), key);
      }
    }
    const char* bottom[3] = {"SPACE", "DELETE", "SEND"};
    const int span[3][2] = {{0, 5}, {5, 8}, {8, 10}};
    const float by = ky + 4 * (kh + 8 * u);
    for (int k = 0; k < 3; ++k) {
      const bool sel = key_row_ == 4 && BottomKey(key_col_) == k;
      const float x0 = l + span[k][0] * kw, x1 = l + span[k][1] * kw;
      dl->AddRectFilled(ImVec2(x0 + 3 * u, by), ImVec2(x1 - 3 * u, by + kh),
                        A(sel ? kRed : k == 2 ? IM_COL32(70, 20, 22, 255) : IM_COL32(40, 38, 38, 255)), 5 * u);
      dl->AddText(g_item, 30 * u, ImVec2((x0 + x1 - Width(g_item, 30 * u, bottom[k])) * 0.5f, by + 16 * u),
                  A(kWhite), bottom[k]);
    }
    dl->AddText(g_body, 22 * u, ImVec2(l, y + h - 50 * u), A(kGrey),
                "X  TYPE      SQUARE  DELETE      TRIANGLE  SPACE      L1  CAPS      OPTIONS  SEND      O  CANCEL");
  }

  // "The installs are done: delete the files they came from?" over the page.
  void DrawLeftoversQuestion(ImDrawList* dl, const ImVec2& size) {
    const float u = u_;
    dl->AddRectFilled(ImVec2(0, 0), size, A(IM_COL32(0, 0, 0, 170)));
    const float w = 1100 * u, x = (size.x - w) * 0.5f, y = size.y * 0.5f - 200 * u;
    dl->AddRectFilled(ImVec2(x, y), ImVec2(x + w, y + 400 * u), A(IM_COL32(14, 12, 12, 250)), 10 * u);
    dl->AddRectFilled(ImVec2(x, y), ImVec2(x + 8 * u, y + 400 * u), A(kRed));
    dl->AddText(g_item, 44 * u, ImVec2(x + 50 * u, y + 40 * u), A(kWhite), "INSTALL COMPLETE");
    char line[96];
    std::snprintf(line, sizeof(line), "%.1f GB", double(leftover_bytes_) / (1024.0 * 1024.0 * 1024.0));
    const std::string text =
        std::string("The disc images, title update and DLC packages it was installed from (and any Xbox avatar "
                    "items, which the game doesn't use) are still in PPSA95454/install/ (") +
        line +
        "). The game doesn't need them any more.\n\nDelete them to free the space, or keep them (they won't "
        "be installed again).";
    dl->AddText(g_body, 28 * u, ImVec2(x + 50 * u, y + 110 * u), A(kText), text.c_str(), nullptr, w - 100 * u);
    dl->AddText(g_item, 34 * u, ImVec2(x + 50 * u, y + 320 * u), A(kWhite), "X  DELETE");
    dl->AddText(g_item, 34 * u, ImVec2(x + 380 * u, y + 320 * u), A(kGrey), "O  KEEP");
  }

  ImU32 A(ImU32 c, float a = -1) const {
    if (a < 0) a = fade_;
    return (c & 0x00FFFFFFu) | (ImU32(float(c >> 24) * std::clamp(a, 0.0f, 1.0f)) << 24);
  }
  ImU32 P(ImU32 c) const { return A(c, page_alpha_); }

  // The picture covering the screen with a slow drift, darkened toward the
  // menu's side, the film's grain over it: a few flickering scratches.
  void DrawBackground(ImDrawList* dl, const ImVec2& size, float dt) {
    const float u = u_;
    dl->AddRectFilled(ImVec2(0, 0), size, IM_COL32(8, 8, 8, 255));
    if (background_) {
      const float s = std::max(size.x / background_.w, size.y / background_.h) * 1.06f;
      const float w = background_.w * s, h = background_.h * s;
      const float drift = std::sin(time_ * 0.05f) * (w - size.x) * 0.5f;
      const ImVec2 p0((size.x - w) * 0.5f + drift, (size.y - h) * 0.5f);
      dl->AddImage(background_.id, p0, ImVec2(p0.x + w, p0.y + h), ImVec2(0, 0), ImVec2(1, 1),
                   A(IM_COL32(255, 255, 255, 200)));
    }
    // the page's dark: the side of the menu on HOME, all of it on the others
    const float dark = page_ == kHome ? 0.0f : 0.74f;
    page_dark_ += (dark - page_dark_) * std::min(1.0f, dt * 6.0f);
    dl->AddRectFilledMultiColor(ImVec2(0, 0), ImVec2(size.x * 0.62f, size.y), A(IM_COL32(0, 0, 0, 235)),
                                A(IM_COL32(0, 0, 0, 0)), A(IM_COL32(0, 0, 0, 0)), A(IM_COL32(0, 0, 0, 235)));
    dl->AddRectFilled(ImVec2(0, 0), size, A(IM_COL32(0, 0, 0, int(255 * page_dark_))));
    // top and bottom bars' shade
    dl->AddRectFilledMultiColor(ImVec2(0, 0), ImVec2(size.x, 170 * u), A(IM_COL32(0, 0, 0, 200)),
                                A(IM_COL32(0, 0, 0, 200)), A(IM_COL32(0, 0, 0, 0)), A(IM_COL32(0, 0, 0, 0)));
    dl->AddRectFilledMultiColor(ImVec2(0, size.y - 150 * u), size, A(IM_COL32(0, 0, 0, 0)), A(IM_COL32(0, 0, 0, 0)),
                                A(IM_COL32(0, 0, 0, 210)), A(IM_COL32(0, 0, 0, 210)));
    // the film's grain: a few flickering scratches
    for (int i = 0; i < 6; ++i) {
      const float r = std::fmod(std::sin(float(i) * 12.9898f + std::floor(time_ * 12.0f) * 78.233f) * 43758.5453f, 1.0f);
      const float x = std::fabs(r) * size.x;
      dl->AddLine(ImVec2(x, 0), ImVec2(x + 3 * u, size.y), A(IM_COL32(255, 255, 255, 10)), 1.0f * u);
    }
  }

  void DrawTabs(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, y = 58 * u, ts = 40 * u;
    float x = 140 * u;
    // the L1 / R1 marks either side
    auto button = [&](float bx, const char* label) {
      const float bw = Width(g_body, 20 * u, label) + 18 * u;
      dl->AddRect(ImVec2(bx, y + 9 * u), ImVec2(bx + bw, y + 37 * u), A(kGrey), 6 * u, 0, 1.5f * u);
      dl->AddText(g_body, 20 * u, ImVec2(bx + 9 * u, y + 12 * u), A(kGrey), label);
      return bw;
    };
    x += button(x, "L1") + 30 * u;
    for (int i = 0; i < kPages; ++i) {
      const float w = Width(g_item, ts, kPageNames[i]);
      if (i == page_) {
        tab_target_x_ = x, tab_target_w_ = w;
        if (tab_w_ == 0) tab_x_ = x, tab_w_ = w;
      }
      dl->AddText(g_item, ts, ImVec2(x, y), A(i == page_ ? kWhite : kGrey), kPageNames[i]);
      x += w + 52 * u;
    }
    button(x - 22 * u, "R1");
    // the project's name at the right
    const char* name = "MAX PAYNE 3  REPRESCRIBED";
    const float nw = Width(g_body, 20 * u, name);
    dl->AddText(g_body, 20 * u, ImVec2(size.x - 140 * u - nw, y + 12 * u), A(kDim), name);
    dl->AddRectFilled(ImVec2(tab_x_, y + 56 * u), ImVec2(tab_x_ + tab_w_, y + 61 * u), A(kRed));
    dl->AddRectFilled(ImVec2(140 * u, y + 61 * u), ImVec2(size.x - 140 * u, y + 62 * u), A(IM_COL32(255, 255, 255, 40)));
  }

  void DrawFooter(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, y = size.y - 92 * u;
    dl->AddRectFilled(ImVec2(140 * u, y - 18 * u), ImVec2(size.x - 140 * u, y - 17 * u), A(IM_COL32(255, 255, 255, 40)));
    dl->AddText(g_body, 22 * u, ImVec2(140 * u, y), A(kRed),
                Upper(Date(int64_t(std::time(nullptr)), "%a / %b %d / %I:%M %p")).c_str());
    std::string hints;
    switch (page_) {
      case kHome: hints = "X  PLAY      L1 R1  PAGES"; break;
      case kContent: hints = "UP DOWN  MOVE      X  INSTALL      TRIANGLE  UNINSTALL      O  BACK      L1 R1  PAGES"; break;
      case kSaves: hints = "UP DOWN  MOVE      X  SELECT      O  BACK      L1 R1  PAGES"; break;
      case kSettings: hints = "UP DOWN  MOVE      LEFT RIGHT  CHANGE      O  BACK      L1 R1  PAGES"; break;
      case kTrophies: hints = "UP DOWN  MOVE      LEFT RIGHT  PAGE UP / DOWN      O  BACK      L1 R1  PAGES"; break;
      default: hints = "O  BACK      L1 R1  PAGES"; break;
    }
    dl->AddText(g_body, 22 * u, ImVec2(size.x - 140 * u - Width(g_body, 22 * u, hints), y), A(kGrey), hints.c_str());
  }

  void DrawHome(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, left = 140 * u + page_dx_;
    float y = 250 * u;
    if (logo_) {
      const float h = 150 * u, w = logo_.w * h / logo_.h;
      dl->AddImage(logo_.id, ImVec2(left, y), ImVec2(left + w, y + h), ImVec2(0, 0), ImVec2(1, 1), P(kWhite));
      y += h + 16 * u;
    } else {
      dl->AddText(g_title, 120 * u, ImVec2(left, y), P(kWhite), "MAX PAYNE");
      const float w = Width(g_title, 120 * u, "MAX PAYNE");
      dl->AddText(g_title, 120 * u, ImVec2(left + w + 30 * u, y), P(kRed), "3");
      y += 156 * u;
    }
    // the project's name, letter-spaced under the title
    Spaced(dl, g_item, 34 * u, ImVec2(left + 4 * u, y), P(kGrey), "REPRESCRIBED", 9 * u);
    y += 60 * u;
    dl->AddRectFilled(ImVec2(left, y), ImVec2(left + 90 * u, y + 5 * u), P(kRed));
    y += 44 * u;
    // PLAY, with the pulsing bar
    const float ps = 96 * u;
    const float pulse = 0.7f + 0.3f * std::sin(time_ * 5.0f);
    dl->AddRectFilled(ImVec2(left - 38 * u, y + ps * 0.2f), ImVec2(left - 24 * u, y + ps * 0.86f),
                      P((kRed & 0x00FFFFFFu) | (ImU32(255 * pulse) << 24)));
    const bool installing = install::TheJob().Busy();
    dl->AddText(g_item, ps, ImVec2(left, y), P(Ready() && !installing ? kWhite : kDim), "PLAY");
    y += ps + 18 * u;
    if (installing) {
      y = DrawJob(dl, left, y, 900 * u) + 20 * u;
    } else if (!Ready()) {
      // what's missing, and where it goes
      missing_flash_ = std::max(0.0f, missing_flash_ - 0.03f);
      const int d = settings_.disc == 2 ? 1 : 0;
      const char* what =
          !installed_.disc[d]
              ? (d ? "DISC 2 ISN'T INSTALLED\nPut your disc 2 image (.iso) or its files in PPSA95454/install/ and "
                     "open the app again: it installs by itself. Or choose disc 1 on the SETTINGS page."
                   : "THE GAME ISN'T INSTALLED\nPut your Max Payne 3 disc images (.iso, any name) or the discs' "
                     "files in PPSA95454/install/ and open the app again: they install by themselves. The "
                     "CONTENT page shows what's there.")
              : "TITLE UPDATE 9 ISN'T INSTALLED\nPut Title Update 9's package (tu00000001_00000000, any name) in "
                "PPSA95454/install/ and open the app again: it installs by itself.";
      const float pw = 900 * u, th = g_body->CalcTextSizeA(26 * u, FLT_MAX, pw, what).y;
      dl->AddRectFilled(ImVec2(left - 16 * u, y - 12 * u), ImVec2(left + pw + 16 * u, y + th + 12 * u),
                        P(IM_COL32(40, 6, 6, ImU32(200 + 55 * missing_flash_))), 6 * u);
      dl->AddRectFilled(ImVec2(left - 16 * u, y - 12 * u), ImVec2(left - 10 * u, y + th + 12 * u), P(kRed));
      dl->AddText(g_body, 26 * u, ImVec2(left, y), P(kWhite), what, nullptr, pw);
      y += th + 42 * u;
    } else {
      char where[160];
      std::snprintf(where, sizeof(where), "Disc %d.  Title Update 9, %s and your saves load with the game.",
                    settings_.disc == 2 ? 2 : 1,
                    installed_.dlc == 1 ? "1 DLC pack" : (std::to_string(installed_.dlc) + " DLC packs").c_str());
      dl->AddText(g_body, 26 * u, ImVec2(left, y), P(kGrey), where);
      y += 54 * u;
    }
    if (trophies_.loaded) {
      // (on a dark panel: the picture behind it is busy)
      dl->AddRectFilled(ImVec2(left - 16 * u, y - 12 * u), ImVec2(left + 560 * u, y + 48 * u),
                        P(IM_COL32(8, 8, 8, 185)), 6 * u);
      dl->AddRectFilled(ImVec2(left - 16 * u, y - 12 * u), ImVec2(left - 11 * u, y + 48 * u), P(kRed));
      trophies::DrawCup(dl, ImVec2(left + 14 * u, y + 16 * u), 30 * u,
                        trophies_.Platinum() ? trophies::Grade::kPlatinum : trophies::Grade::kGold, page_alpha_,
                        trophies_.earned > 0);
      char line[96];
      std::snprintf(line, sizeof(line), "%d of %d trophies  -  %d%%", trophies_.earned, trophies_.earnable,
                    trophies_.Percent());
      dl->AddText(g_body, 26 * u, ImVec2(left + 40 * u, y), P(kWhite), line);
    }
  }

  // The running job's title and progress bar, or the last one's outcome for a while; the y under it.
  float DrawJob(ImDrawList* dl, float x, float y, float w) {
    const float u = u_;
    auto& job = install::TheJob();
    if (job.Busy()) {
      const float pr = job.Progress();
      char line[64];
      std::snprintf(line, sizeof(line), "%d%%", int(pr * 100));
      dl->AddRectFilled(ImVec2(x - 16 * u, y - 12 * u), ImVec2(x + w + 16 * u, y + 78 * u), P(IM_COL32(8, 8, 8, 200)),
                        6 * u);
      // (a long disc image name cut short of the percentage)
      const float pw = Width(g_body, 26 * u, line);
      dl->AddText(g_body, 26 * u, ImVec2(x, y), P(kWhite),
                  Fit(g_body, 26 * u, Upper(job.Title()), w - pw - 30 * u).c_str());
      dl->AddText(g_body, 26 * u, ImVec2(x + w - pw, y), P(kRed), line);
      dl->AddRectFilled(ImVec2(x, y + 46 * u), ImVec2(x + w, y + 54 * u), P(kDim));
      dl->AddRectFilled(ImVec2(x, y + 46 * u), ImVec2(x + w * pr, y + 54 * u), P(kRed));
      return y + 90 * u;
    }
    const std::string result = job.Result();
    if (!result.empty() && time_ - result_t_ < 12.0f) {
      const float th = g_body->CalcTextSizeA(26 * u, FLT_MAX, w, result.c_str()).y;
      dl->AddRectFilled(ImVec2(x - 16 * u, y - 12 * u), ImVec2(x + w + 16 * u, y + th + 12 * u),
                        P(IM_COL32(8, 8, 8, 200)), 6 * u);
      dl->AddRectFilled(ImVec2(x - 16 * u, y - 12 * u), ImVec2(x - 10 * u, y + th + 12 * u),
                        P(job.Failed() ? IM_COL32(230, 50, 40, 255) : kWhite));
      dl->AddText(g_body, 26 * u, ImVec2(x, y), P(kWhite), result.c_str(), nullptr, w);
      return y + th + 30 * u;
    }
    return y;
  }

  static std::string Short(std::string s, size_t n) {
    if (s.size() > n) s = s.substr(0, n - 3) + "...";
    return s;
  }

  void DrawContent(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, left = 140 * u + page_dx_;
    float y = 200 * u;
    const std::string dlc_name =
        installed_.dlc ? "DLC  (" + std::to_string(installed_.dlc) + (installed_.dlc == 1 ? " PACK)" : " PACKS)") : "DLC";
    const std::string names[kContentRows] = {"DISC 1", "DISC 2", "TITLE UPDATE 9", dlc_name, "HD TEXTURES"};
    const char* help[kContentRows] = {
        "Max Payne 3's first disc. Put your disc images (.iso, any name) or folders of the discs' files in "
        "PPSA95454/install/: they install into PPSA95454/game/ and game2/ when the app opens, or with X here. An "
        "image is deleted once it's in.",
        "The second disc: the second half of the story. The game asks for it when the story gets there, and "
        "starts on it again from then on (the SETTINGS page's DISC).",
        "Required: the game runs Title Update 9's code. Put its package (tu00000001_00000000, any name) in "
        "PPSA95454/install/: it patches both discs. Other title updates can't be used.",
        "Optional: the DLC packs (multiplayer maps, characters and weapons). Put their packages (any names) in "
        "PPSA95454/install/: each installs by itself. TRIANGLE removes them all.",
        "Optional: the PC version's HD textures in place of the Xbox 360's. Put the Nexus texture pack's folder (as "
        "downloaded and extracted) in PPSA95454/install/: the app makes the HD textures from it (about 8 GB, a few "
        "minutes) and deletes the pack's files afterwards. The PC game's own folder adds the textures the pack "
        "doesn't have. Turn them on or off on the SETTINGS page."};
    for (int r = 0; r < kContentRows; ++r) {
      const bool sel = r == content_row_, in = RowInstalled(r);
      const install::Source* src = SourceFor(r);
      const float fs = 40 * u;
      if (sel) dl->AddRectFilled(ImVec2(left - 30 * u, y + 8 * u), ImVec2(left - 20 * u, y + fs), P(kRed));
      dl->AddText(g_item, fs, ImVec2(left, y), P(sel ? kWhite : kGrey), names[r].c_str());
      std::string status = in && !(r == kRowDlc && src) ? "INSTALLED"
                           : src                        ? "READY TO INSTALL"
                           : r == kRowUpdate && (installed_.update[0] || installed_.update[1]) ? "INSTALLED (ONE DISC)"
                                                                                               : "NOT INSTALLED";
      if (r == kRowDlc && src) status = std::to_string(DlcWaiting()) + " READY TO INSTALL";
      if (sel && Confirming(100 + r)) status = "PRESS TRIANGLE AGAIN TO UNINSTALL";
      dl->AddText(g_body, 28 * u, ImVec2(left + 430 * u, y + 8 * u),
                  P(in ? kWhite : src ? kWhite : (r != kRowDlc && r != kRowDisc2 ? IM_COL32(230, 90, 70, 255) : kGrey)),
                  status.c_str());
      y += fs + 6 * u;
      if (src && !in) {
        dl->AddText(g_body, 22 * u, ImVec2(left + 430 * u, y), P(kGrey), Short(src->label, 44).c_str());
        y += 30 * u;
      }
      y += 18 * u;
    }
    y += 10 * u;
    y = DrawJob(dl, left, y, 820 * u) + 10 * u;
    // the rest of install/: what can't be installed, and why
    std::vector<std::string> notes;
    int avatar_items = 0;
    for (const auto& s : sources_) {
      if (s.kind == install::Kind::kAvatarItem) ++avatar_items;
      if (s.kind == install::Kind::kArchive)
        notes.push_back(Short(s.label, 40) + "  -  extract it on a PC first (7-Zip)");
      else if (s.kind == install::Kind::kOtherUpdate)
        notes.push_back(Short(s.label, 40) + "  -  not Title Update 9");
      else if (s.kind == install::Kind::kOther)
        notes.push_back(Short(s.label, 60));
    }
    if (avatar_items)
      notes.insert(notes.begin(), std::to_string(avatar_items) + (avatar_items == 1 ? " Xbox avatar item" : " Xbox avatar items") +
                                      "  -  not used by the game");
    if (!notes.empty()) {
      dl->AddText(g_body, 20 * u, ImVec2(left, y), P(kRed), "ALSO IN PPSA95454/INSTALL");
      y += 34 * u;
      for (size_t i = 0; i < notes.size() && i < 5; ++i, y += 32 * u)
        dl->AddText(g_body, 24 * u, ImVec2(left, y), P(kGrey), notes[i].c_str());
    }
    // the focused row's help, at the right
    const float px = 1180 * u + page_dx_, pw = size.x - 140 * u - px;
    dl->AddRectFilled(ImVec2(px - 40 * u, 200 * u), ImVec2(px - 39 * u, 760 * u), P(IM_COL32(255, 255, 255, 40)));
    dl->AddText(g_body, 26 * u, ImVec2(px, 204 * u), P(kText), help[content_row_], nullptr, pw);
  }

  void DrawSaves(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, left = 140 * u + page_dx_;
    float y = 200 * u;
    char count[64];
    std::snprintf(count, sizeof(count), "%d %s INSTALLED", installed_.saves, installed_.saves == 1 ? "SAVE" : "SAVES");
    dl->AddText(g_body, 22 * u, ImVec2(left, y), P(kRed), count);
    y += 48 * u;
    const auto saves = SaveSources();
    const int rows = kSavesFirst + int(saves.size());
    // (an imported save's file deleted after the install: the list shorter under the selected row)
    saves_row_ = std::min(saves_row_, rows - 1);
    for (int r = 0; r < rows; ++r) {
      const bool sel = r == saves_row_;
      const float fs = 36 * u;
      if (sel) dl->AddRectFilled(ImVec2(left - 30 * u, y + 6 * u), ImVec2(left - 20 * u, y + fs), P(kRed));
      std::string label;
      if (r == kBackupRow) {
        label = "BACK UP SAVES";
      } else if (r == kSendLogsRow) {
        label = "SEND LOGS";
      } else {
        const install::Source* s = saves[size_t(r - kSavesFirst)];
        label = (s->kind == install::Kind::kBackup ? "RESTORE  " : "IMPORT  ") + Short(s->label, 40);
        if (sel && Confirming(200 + r)) label = "PRESS X AGAIN TO CONFIRM";
      }
      dl->AddText(g_item, fs, ImVec2(left, y), P(sel ? kWhite : kGrey), label.c_str());
      y += fs + 22 * u;
      // (the link the logs went to, kept on screen to be copied down)
      if (r == kSendLogsRow) {
        const std::string link = install::TheJob().LogsLink();
        if (!link.empty()) {
          // (in the body font: the link has small letters, and they count)
          dl->AddText(g_body, 30 * u, ImVec2(left, y - 12 * u), P(kRed), "LINK  ");
          dl->AddText(g_body, 30 * u, ImVec2(left + Width(g_body, 30 * u, "LINK  "), y - 12 * u), P(kWhite),
                      link.c_str());
          y += 40 * u;
        }
      }
    }
    y += 10 * u;
    y = DrawJob(dl, left, y, 820 * u) + 10 * u;
    // the saves the app has, newest first
    if (!installed_.save_list.empty()) {
      Spaced(dl, g_body, 19 * u, ImVec2(left, y), P(kRed), "YOUR SAVES", 3 * u);
      y += 36 * u;
      for (size_t i = 0; i < installed_.save_list.size() && i < 6; ++i, y += 34 * u) {
        const auto& sv = installed_.save_list[i];
        std::string name = sv.name;
        std::replace(name.begin(), name.end(), '_', ' ');
        dl->AddText(g_body, 26 * u, ImVec2(left, y), P(kWhite), Short(name, 34).c_str());
        if (sv.modified)
          dl->AddText(g_body, 26 * u, ImVec2(left + 520 * u, y), P(kGrey),
                      Upper(Date(sv.modified, "%b %d %Y  %I:%M %p")).c_str());
      }
    }
    const char* help =
        saves_row_ == kBackupRow
            ? "Copies your saves, your trophies and your settings to PPSA95454/install/backups/. Copy that folder "
              "to a PC or a USB drive before deleting or reinstalling the app."
        : saves_row_ == kSendLogsRow
            ? "For a bug report: type what went wrong, and the app's logs (PPSA95454/logs/, the last few runs) "
              "go to catbox.moe. A link and a QR code come back: the QR code opens a GitHub issue with it all "
              "filled in. Anyone with the link can read the logs; they say how the game ran, not who you are. "
              "Needs the internet and a payload loader (etaHEN or elfldr)."
        : saves[size_t(saves_row_ - kSavesFirst)]->kind == install::Kind::kBackup
            ? "Puts this backup's saves, trophies and settings back, over the ones here."
            : "Imports this Xbox 360 save (a package copied off a 360, with a USB drive or Horizon) into the app. "
              "A save of the same name here is replaced.";
    const char* where =
        "\n\nTo import a save, put the Xbox 360 save package (the file copied off the 360's drive: "
        "Content/<profile>/5454086B/00000001/...) in PPSA95454/install/. A backup made here, copied back into "
        "PPSA95454/install/ (or install/backups/), is listed to restore.";
    const std::string text = std::string(help) + (saves.empty() ? where : "");
    const float px = 1180 * u + page_dx_, pw = size.x - 140 * u - px;
    dl->AddRectFilled(ImVec2(px - 40 * u, 200 * u), ImVec2(px - 39 * u, 760 * u), P(IM_COL32(255, 255, 255, 40)));
    // the logs sent: the QR code of the GitHub issue in place of the help
    const std::string link = install::TheJob().LogsLink();
    UpdateQr(link);
    if (saves_row_ == kSendLogsRow && qr_size_) {
      dl->AddText(g_item, 34 * u, ImVec2(px, 204 * u), P(kWhite), "SCAN TO REPORT IT");
      dl->AddText(g_body, 24 * u, ImVec2(px, 252 * u), P(kText),
                  "With your phone's camera: a GitHub issue opens with what you typed, the logs' link and your "
                  "settings filled in. Press Submit (a GitHub account is needed). Or send the link above with "
                  "your report.",
                  nullptr, pw);
      DrawQr(dl, px, 380 * u, std::min(pw, 560 * u));
      return;
    }
    dl->AddText(g_body, 26 * u, ImVec2(px, 204 * u), P(kText), text.c_str(), nullptr, pw);
  }

  void DrawSettings(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, left = 140 * u + page_dx_;
    float y = 190 * u;
    const auto lines = SettingsLines();
    const char* section = nullptr;
    for (int r = 0; r < kSettingsRows; ++r) {
      const SettingLine& l = lines[size_t(r)];
      if (!section || std::strcmp(section, l.section) != 0) {
        section = l.section;
        if (r) y += 14 * u;
        Spaced(dl, g_body, 19 * u, ImVec2(left, y), P(kRed), section, 3 * u);
        y += 36 * u;
      }
      const bool sel = r == row_;
      const float fs = 36 * u;
      const ImU32 col = !l.enabled ? kDim : sel ? kWhite : kGrey;
      if (sel) dl->AddRectFilled(ImVec2(left - 30 * u, y + 8 * u), ImVec2(left - 20 * u, y + fs), P(kRed));
      dl->AddText(g_item, fs, ImVec2(left, y), P(col), l.label);
      const std::string v = l.slider || !l.enabled ? l.value : "<  " + l.value + "  >";
      dl->AddText(g_item, fs, ImVec2(left + 420 * u, y), P(sel ? (l.enabled ? kRed : kDim) : col), v.c_str());
      if (l.slider) {
        const float x0 = left + 540 * u, w = 260 * u, yy = y + fs * 0.6f;
        dl->AddRectFilled(ImVec2(x0, yy - 3 * u), ImVec2(x0 + w, yy + 3 * u), P(kDim));
        dl->AddRectFilled(ImVec2(x0, yy - 3 * u), ImVec2(x0 + w * settings_.music / 100.0f, yy + 3 * u),
                          P(sel ? kRed : kGrey));
      }
      y += fs + 16 * u;
    }
    // the focused row's help, at the right
    const float px = 1060 * u + page_dx_, pw = 720 * u;
    dl->AddRectFilled(ImVec2(px - 40 * u, 190 * u), ImVec2(px - 39 * u, 760 * u), P(IM_COL32(255, 255, 255, 40)));
    dl->AddText(g_item, 34 * u, ImVec2(px, 190 * u), P(kWhite), lines[size_t(row_)].label);
    dl->AddText(g_body, 28 * u, ImVec2(px, 244 * u), P(kText), lines[size_t(row_)].help, nullptr, pw);
    dl->AddText(g_body, 22 * u, ImVec2(px, 700 * u), P(kDim), "Saved when you press PLAY.");
  }

  void DrawTrophies(ImDrawList* dl, const ImVec2& size, float dt) {
    const float u = u_;
    if (!trophies_.loaded) {
      dl->AddText(g_item, 44 * u, ImVec2(140 * u + page_dx_, 220 * u), P(kWhite), "START THE GAME ONCE");
      dl->AddText(g_body, 28 * u, ImVec2(140 * u + page_dx_, 290 * u), P(kGrey),
                  "The trophy list comes from the game itself: start it once, and the next time the menu opens "
                  "your trophies are here, with the ones you've earned.",
                  nullptr, 1000 * u);
      return;
    }
    const int n = int(items_.size());
    if (!n) return;
    trophy_ = std::clamp(trophy_, 0, n - 1);
    detail_in_ = std::min(1.0f, detail_in_ + dt * 6.0f);

    // the list at the left: group headings, then each trophy
    struct Row {
      int item;  // -1: a heading
      int group;
    };
    std::vector<Row> rows;
    int counts[3] = {};
    for (const Item& it : items_) ++counts[it.group];
    int sel_row = 0;
    for (int i = 0; i < n; ++i) {
      if (i == 0 || items_[size_t(i) - 1].group != items_[size_t(i)].group) rows.push_back({-1, items_[size_t(i)].group});
      if (i == trophy_) sel_row = int(rows.size());
      rows.push_back({i, items_[size_t(i)].group});
    }
    const float row_h = 64 * u, top = 172 * u, bottom = size.y - 130 * u;
    const float view = bottom - top;
    const float want = std::clamp(sel_row * row_h - view * 0.4f, 0.0f, std::max(0.0f, rows.size() * row_h - view));
    scroll_ += (want - scroll_) * std::min(1.0f, dt * 12.0f);
    const float x = 140 * u + page_dx_, right = 940 * u + page_dx_;
    static const char* const kGroupNames[] = {"EARNED", "NOT EARNED YET", "MULTIPLAYER  -  ONLINE ONLY"};
    dl->PushClipRect(ImVec2(0, top), ImVec2(size.x, bottom), true);
    for (size_t r = 0; r < rows.size(); ++r) {
      const float ry = top + r * row_h - scroll_;
      if (ry + row_h < top || ry > bottom) continue;
      if (rows[r].item < 0) {
        const std::string head = std::string(kGroupNames[rows[r].group]) + "   " + std::to_string(counts[rows[r].group]);
        dl->AddText(g_body, 21 * u, ImVec2(x, ry + 30 * u), P(kRed), head.c_str());
        continue;
      }
      const Item& it = items_[size_t(rows[r].item)];
      const bool sel = rows[r].item == trophy_, earned = Earned(it);
      if (sel) dl->AddRectFilled(ImVec2(x - 16 * u, ry + 4 * u), ImVec2(right + 10 * u, ry + row_h - 4 * u),
                                 P(IM_COL32(196, 22, 28, 52)), 6 * u);
      Icon(dl, it, x, ry + 8 * u, 48 * u, page_alpha_);
      const ImU32 col = sel ? kWhite : earned ? IM_COL32(210, 206, 200, 255) : kGrey;
      dl->AddText(g_item, 28 * u, ImVec2(x + 70 * u, ry + 13 * u), P(col),
                  Fit(g_item, 28 * u, NameOf(it), right - x - 130 * u).c_str());
      trophies::DrawCup(dl, ImVec2(right - 18 * u, ry + row_h * 0.5f), 30 * u, GradeOf(it), page_alpha_, earned);
    }
    dl->PopClipRect();

    // how far along, then the focused trophy
    const float px = 1040 * u + page_dx_, pw = size.x - 140 * u - px;
    float py = 180 * u;
    dl->AddRectFilled(ImVec2(px - 50 * u, top), ImVec2(px - 49 * u, bottom), P(IM_COL32(255, 255, 255, 40)));
    const std::string pct = std::to_string(trophies_.Percent()) + "%";
    dl->AddText(g_title, 72 * u, ImVec2(px, py - 10 * u), P(kWhite), pct.c_str());
    const float pctw = Width(g_title, 72 * u, pct);
    const std::string of = std::to_string(trophies_.earned) + " of " + std::to_string(trophies_.earnable) + " earned";
    dl->AddText(g_body, 26 * u, ImVec2(px + pctw + 24 * u, py + 34 * u), P(kGrey), of.c_str());
    float rx = px + pw;
    for (int g = 0; g < 3; ++g) {  // (bronze at the right, gold at the left)
      const std::string c = std::to_string(trophies_.grade_earned[g]) + "/" + std::to_string(trophies_.grade_total[g]);
      const float cw = Width(g_body, 24 * u, c);
      dl->AddText(g_body, 24 * u, ImVec2(rx - cw, py + 34 * u), P(trophies_.grade_earned[g] ? kWhite : kDim), c.c_str());
      trophies::DrawCup(dl, ImVec2(rx - cw - 20 * u, py + 48 * u), 26 * u, trophies::Grade(g), page_alpha_,
                        trophies_.grade_earned[g] > 0);
      rx -= cw + 60 * u;
    }
    py += 92 * u;
    dl->AddRectFilled(ImVec2(px, py), ImVec2(px + pw, py + 8 * u), P(IM_COL32(255, 255, 255, 30)));
    const float f = trophies_.points ? float(trophies_.points_earned) / float(trophies_.points) : 0.0f;
    if (f > 0) dl->AddRectFilled(ImVec2(px, py), ImVec2(px + std::max(4 * u, pw * f), py + 8 * u),
                                 P(trophies_.Platinum() ? trophies::GradeColor(trophies::Grade::kPlatinum) : kRed));
    py += 54 * u;

    const Item& it = items_[size_t(trophy_)];
    const float da = page_alpha_ * detail_in_;
    const float dx = px + (1 - detail_in_) * 14 * u;
    Icon(dl, it, dx, py, 112 * u, da);
    const trophies::Grade g = GradeOf(it);
    const std::string grade = Upper(std::string(trophies::GradeName(g)) + " trophy");
    dl->AddText(g_body, 20 * u, ImVec2(dx + 136 * u, py + 8 * u), trophies::GradeColor(g, da), grade.c_str());
    dl->AddText(g_item, 36 * u, ImVec2(dx + 136 * u, py + 34 * u), A(kWhite, da), NameOf(it).c_str(), nullptr,
                pw - 136 * u);
    py += 138 * u;
    const std::string text = TextOf(it);
    dl->AddText(g_body, 27 * u, ImVec2(dx, py), A(kText, da), text.c_str(), nullptr, pw);
    py += g_body->CalcTextSizeA(27 * u, FLT_MAX, pw, text.c_str()).y + 34 * u;
    const trophies::Trophy* t = it.index < 0 ? nullptr : &trophies_.trophies[size_t(it.index)];
    std::string status;
    if (Earned(it)) status = "Earned " + Date(t ? t->earned : trophies_.last_earned, "%b %d, %Y");
    else if (!t) status = std::to_string(trophies_.earnable - trophies_.earned) + " to go";
    else if (t->online()) status = "Not available offline";
    else status = "Not earned yet";
    dl->AddText(g_body, 20 * u, ImVec2(dx, py), A(kDim, da), "STATUS");
    dl->AddText(g_body, 24 * u, ImVec2(dx + 170 * u, py - 2 * u), A(kWhite, da), status.c_str());
    if (t) {
      py += 40 * u;
      dl->AddText(g_body, 20 * u, ImVec2(dx, py), A(kDim, da), "GAMERSCORE");
      dl->AddText(g_body, 24 * u, ImVec2(dx + 170 * u, py - 2 * u), A(kWhite, da),
                  (std::to_string(t->gamerscore) + "G").c_str());
    }
  }

  void DrawCredits(ImDrawList* dl, const ImVec2& size) {
    const float u = u_, x = 140 * u + page_dx_, w = 820 * u;
    float y = 180 * u;
    auto entry = [&](const char* name, const char* text) {
      dl->AddText(g_item, 32 * u, ImVec2(x, y), P(kWhite), name);
      y += 44 * u;
      dl->AddText(g_body, 24 * u, ImVec2(x, y), P(kGrey), text, nullptr, w);
      y += g_body->CalcTextSizeA(24 * u, FLT_MAX, w, text).y + 28 * u;
    };
    // the project
    dl->AddText(g_title, 60 * u, ImVec2(x, y - 6 * u), P(kWhite), "MAX PAYNE 3");
    const float tw = Width(g_title, 60 * u, "MAX PAYNE 3");
    Spaced(dl, g_item, 30 * u, ImVec2(x + tw + 22 * u, y + 24 * u), P(kRed), "REPRESCRIBED", 6 * u);
    y += 96 * u;
    entry("BY CHAOSTHEORY1075", "github.com/ChaosTheory1075/mp3-ps5");
    dl->AddText(g_body, 20 * u, ImVec2(x, y), P(kRed), "ALL THE CREDIT GOES TO");
    y += 40 * u;
    entry("GTA IV FOR PS5  -  ELRIPALDA",
          "The PS5 port this one is built on, top to bottom: the PS5 runtime, graphics and sound, the hub this menu "
          "copies, the trophies and their pop-up, and the installer payload. github.com/elripalda/gta4-ps5");
    entry("LIBERTY RECOMPILED  -  OZORDI  /  VADUUR",
          "OZORDI's recompilation of GTA IV and vaduur's LibertyRecompAndroid, which GTA IV for PS5 starts from.");
    entry("REXGLUE AND XENIA",
          "The static recompiler and runtime under all of it, and the Xenia team's Xbox 360 emulator it grew from "
          "(Xenia Canary's XMA decoding is what plays the game's sound here).");
    entry("CRACKDOWN RECOMPILED  -  SAULOB",
          "Whose ReXGlue patches and frame rate work this port's GPU and timing fixes come from. "
          "github.com/saulob/crackdown-xbox360-recompiled");
    entry("CRACKDOWN: REACTIVATED",
          "The sister port whose menu pages, installer, trophy list and pop-up this one shares.");
    entry("HD TEXTURES  -  MDSXXX1",
          "Max Payne 3 Texture Pack V4 (nexusmods.com/maxpayne3/mods/275, with ElPicoleto's blood effects), which "
          "the HD textures are made from on the PS5.");

    const float px = 1060 * u + page_dx_, pw = size.x - 140 * u - px;
    float py = 180 * u;
    // the shout-out, on a panel of its own
    {
      const char* thanks =
          "For Max Payne: Remedy's noir that started it all, and Rockstar's third chapter - Sao Paulo, the bullet "
          "time, the cutscenes that never cut away. Still the best shootdodge there is.";
      const float th = g_body->CalcTextSizeA(26 * u, FLT_MAX, pw - 48 * u, thanks).y;
      dl->AddRectFilled(ImVec2(px - 24 * u, py - 16 * u), ImVec2(px + pw, py + 96 * u + th),
                        P(IM_COL32(8, 8, 8, 170)), 6 * u);
      dl->AddRectFilled(ImVec2(px - 24 * u, py - 16 * u), ImVec2(px - 18 * u, py + 96 * u + th), P(kRed));
      dl->AddText(g_body, 20 * u, ImVec2(px, py), P(kRed), "SPECIAL THANKS");
      dl->AddText(g_item, 40 * u, ImVec2(px, py + 30 * u), P(kWhite), "ROCKSTAR GAMES  &  REMEDY");
      dl->AddText(g_body, 26 * u, ImVec2(px, py + 84 * u), P(kGrey), thanks, nullptr, pw - 48 * u);
      py += 96 * u + th + 46 * u;
    }
    if (!song_.empty()) {
      dl->AddText(g_body, 20 * u, ImVec2(px, py), P(kRed), "MENU SONG");
      dl->AddText(g_body, 24 * u, ImVec2(px, py + 32 * u), P(kGrey), Fit(g_body, 24 * u, song_, pw).c_str());
      py += 90 * u;
    }
    dl->AddText(g_body, 20 * u, ImVec2(px, py), P(kRed), "PLEASE NOTE");
    py += 36 * u;
    const char* note =
        "An unofficial fan project, not affiliated with or endorsed by Rockstar Games, Take-Two Interactive, Remedy "
        "Entertainment or Sony. Max Payne and all related names belong to their owners. No game files are included: "
        "you need your own copy of Max Payne 3 for the Xbox 360.";
    dl->AddText(g_body, 24 * u, ImVec2(px, py), P(kGrey), note, nullptr, pw);
    py += g_body->CalcTextSizeA(24 * u, FLT_MAX, pw, note).y + 36 * u;
    dl->AddText(g_body, 20 * u, ImVec2(px, py), P(kRed), "BUILT WITH");
    py += 36 * u;
    dl->AddText(g_body, 22 * u, ImVec2(px, py), P(kGrey),
                "PS5 Payload SDK  /  Mesa RADV (Vulkan)  /  Dear ImGui\nstb_image, dr_mp3, zlib, xxHash  /  Oswald and "
                "Inter (SIL Open Font License)\nBlackBearReloaded's PS5 AT9 Converter (the home screen music)",
                nullptr, pw);
  }

  // ---- files ----

  void StartMusic(const fs::path& dir) {
    std::error_code ec;
    if (!fs::is_directory(dir, ec)) return;
    for (const auto& e : fs::directory_iterator(dir, ec)) {
      if (!e.is_regular_file() || !gta4ps5::sound::CanOpen(e.path())) continue;
      if (!gta4ps5::sound::StartOutput()) {
        REXLOG_WARN("Max Payne 3 menu: no audio port for the menu's song");
        return;
      }
      if (auto src = gta4ps5::sound::Open(e.path())) {
        music_ = gta4ps5::sound::NewVoice();
        music_->SetVolume(settings_.music / 100.0f);
        music_->Play(std::move(src), true);
        song_ = e.path().stem().string();
        REXLOG_INFO("Max Payne 3 menu: playing {}", e.path().filename().string());
        return;
      }
    }
  }

  Image Load(const fs::path& file) {
    if (!immediate_) return {};
    int w = 0, h = 0, n = 0;
    unsigned char* px = stbi_load(file.string().c_str(), &w, &h, &n, 4);
    if (!px) return {};
    auto tex = immediate_->CreateTexture(uint32_t(w), uint32_t(h), rex::ui::ImmediateTextureFilter::kLinear, false, px);
    stbi_image_free(px);
    if (!tex) return {};
    Image img{reinterpret_cast<ImTextureID>(tex.get()), float(w), float(h)};
    g_textures->push_back(std::move(tex));
    return img;
  }

  rex::ui::ImmediateDrawer* immediate_ = nullptr;
  fs::path app_, user_data_;
  std::function<void(MenuChoice)> play_;
  Settings settings_;
  Pad pad_;
  Image background_, logo_;
  gta4ps5::sound::Voice* music_ = nullptr;
  std::string song_;
  bool has_disc2_ = false, has_textures_ = false;
  install::Installed installed_;
  std::vector<install::Source> sources_;
  std::set<std::string> tried_;  // (installed by themselves once a session)
  uint32_t job_generation_ = 0;
  int content_row_ = 0, saves_row_ = 0, confirm_key_ = -1;
  float result_t_ = -100, confirm_t_ = -100, missing_flash_ = 0;
  bool ask_leftovers_ = false;
  uint64_t leftover_bytes_ = 0;
  // SEND LOGS: the report being typed (the keyboard over the page), what was sent with the logs,
  // and the QR code of the GitHub issue for the link they went to
  bool reporting_ = false, caps_ = true;
  int key_row_ = 1, key_col_ = 0;
  std::string note_, sent_note_, sent_settings_, qr_link_;
  std::vector<uint8_t> qr_;  // (modules, row by row; 0 when it didn't fit)
  int qr_size_ = 0;
  Page page_ = kHome;
  int row_ = kFrameRate, trophy_ = 0;
  trophies::List trophies_;
  std::vector<Item> items_;
  std::map<uint32_t, Image> pictures_;
  int pictures_this_frame_ = 0;
  float time_ = 0, leave_t_ = 0, fade_ = 0, u_ = 1, page_in_ = 1, page_alpha_ = 1, page_dx_ = 0;
  float tab_x_ = 0, tab_w_ = 0, tab_target_x_ = 0, tab_target_w_ = 0, page_dark_ = 0, scroll_ = 0, detail_in_ = 1;
  bool leaving_ = false, done_ = false;
};

ImFont* AddFont(ImFontAtlas* atlas, const unsigned char* begin, const unsigned char* end, float size) {
  ImFontConfig cfg;
  cfg.FontDataOwnedByAtlas = false;  // (built into the app)
  cfg.OversampleH = 2;
  return atlas->AddFontFromMemoryTTF(const_cast<unsigned char*>(begin), int(end - begin), size, &cfg);
}

}  // namespace

void ConfigureMenuFonts(ImFontAtlas* atlas) {
  // (at the 4K screen's sizes: drawn smaller on a 1080p one)
  g_title = AddFont(atlas, mp3ps5_font_title, mp3ps5_font_title_end, 240.0f);
  g_item = AddFont(atlas, mp3ps5_font_item, mp3ps5_font_item_end, 128.0f);
  g_body = AddFont(atlas, mp3ps5_font_body, mp3ps5_font_body_end, 56.0f);
}

ImFont* MenuBodyFont() { return g_body; }
ImFont* MenuItemFont() { return g_item; }

void ShowMenu(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate, const fs::path& app,
              std::function<void(MenuChoice)> play) {
  REXLOG_INFO("Max Payne 3 menu: shown");
  new MenuDialog(drawer, immediate, app, std::move(play));  // (the dialog owns itself)
}

}  // namespace mp3ps5

#endif  // REX_PLATFORM_PS5
