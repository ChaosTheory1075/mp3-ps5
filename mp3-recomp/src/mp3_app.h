// Max Payne 3 - ReXGlue recompiled app.
//
// On the PS5 the app folder is /app0 (the homebrew folder): the game's files
// in /app0/game (disc 1's files, with the title update's default.xexp beside
// default.xex), the title update's data in /app0/game/update, DLC packages in
// /app0/dlc and saves in /app0/saves. It opens on the menu (ps5/mp3_menu.h);
// achievements show as PS5 trophies (ps5/mp3_trophies.h).

#pragma once

#include <filesystem>
#include <functional>
#include <optional>

#include <rex/rex_app.h>

class Mp3App : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;

  static std::unique_ptr<rex::ui::WindowedApp> Create(rex::ui::WindowedAppContext& ctx);

 protected:
  std::optional<rex::PathConfig> OnFinalizePaths(const rex::PathConfig& defaults,
                                                 std::function<void(rex::PathConfig)> resume) override;
  void OnPreSetup(rex::RuntimeConfig& config) override;
  void OnPostSetup() override;
  void OnPostLaunchModule(rex::system::XThread* thread) override;
  void OnConfigureFonts(ImFontAtlas* atlas) override;
  std::unique_ptr<rex::ui::AchievementNotificationDialog> CreateAchievementNotificationDialog() override;
};
