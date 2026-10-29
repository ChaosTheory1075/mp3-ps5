// Max Payne 3 RePrescribed (PS5) - the menu the app opens on (mp3_menu.cpp),
// laid out as Crackdown PS5's and GTA IV PS5's hub: its pages HOME (PLAY),
// SETTINGS (the disc, the frame rate, the HD textures, the menu music's
// level), TROPHIES (the achievements, earned and not: mp3_trophies.h) and
// CREDITS, over hub/background.jpg with the menu song (hub/music/) playing.
#pragma once

#include <rex/platform.h>
#if REX_PLATFORM_PS5

#include <cstdint>
#include <filesystem>
#include <functional>

struct ImFontAtlas;
struct ImFont;
namespace rex::ui {
class ImGuiDrawer;
class ImmediateDrawer;
}  // namespace rex::ui

namespace mp3ps5 {

// What the menu chose, for this start.
struct MenuChoice {
  uint32_t disc = 1;
};

// The menu's fonts (from OnConfigureFonts), and two of them for the trophy popup.
void ConfigureMenuFonts(ImFontAtlas* atlas);
ImFont* MenuBodyFont();
ImFont* MenuItemFont();

// Shows the menu; `play` runs (on the UI thread) on PLAY, its settings applied
// (the frame rate's and HD textures' cvars) and saved in app/menu.ini.
void ShowMenu(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate, const std::filesystem::path& app,
              std::function<void(MenuChoice)> play);

}  // namespace mp3ps5

#endif  // REX_PLATFORM_PS5
