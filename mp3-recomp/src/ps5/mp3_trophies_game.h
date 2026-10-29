// Max Payne 3 PS5 - its achievements as trophies, in the game (see
// mp3_trophies.h for the whole of it; from gta4-ps5's trophies_game.h):
//
//  - WriteTrophyCatalog: as the game starts, its achievements' list and
//    pictures (from default.xex) go to user_data/achievements, for the menu's
//    Trophies page;
//  - TrophyPopup: an unlock shows in the top-right corner, where the PS5 shows
//    its trophies - the achievement's picture, its grade's cup and its name,
//    with a chime - instead of the runtime's toast. The last single-player
//    one brings the platinum's after it.
#pragma once

#include <rex/platform.h>
#if REX_PLATFORM_PS5

#include <chrono>
#include <deque>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>

#include <rex/ui/overlay/achievement_icon_cache.h>
#include <rex/ui/overlay/achievement_notification.h>

namespace rex {
class Runtime;
}
namespace rex::ui {
class ImmediateDrawer;
class ImmediateTexture;
}  // namespace rex::ui

namespace mp3ps5 {

void WriteTrophyCatalog(rex::Runtime* runtime, const std::filesystem::path& user_data);

class TrophyPopup final : public rex::ui::AchievementNotificationDialog {
 public:
  TrophyPopup(rex::ui::ImGuiDrawer* drawer, rex::ui::ImmediateDrawer* immediate, rex::Runtime* runtime);
  ~TrophyPopup() override;

  // (from the thread that unlocked it)
  void Push(const rex::system::AchievementEvent& event) override;

 protected:
  void OnDraw(ImGuiIO& io) override;

 private:
  struct Card {
    rex::system::AchievementInfo achievement;
    bool platinum = false;
    std::optional<std::chrono::steady_clock::time_point> shown;
  };
  void Chime(bool platinum);
  rex::ui::ImmediateTexture* TitlePicture();

  rex::Runtime* runtime_ = nullptr;
  rex::ui::ImmediateDrawer* immediate_ = nullptr;
  rex::ui::AchievementIconCache icons_;
  std::unique_ptr<rex::ui::ImmediateTexture> title_picture_;
  bool title_picture_tried_ = false;
  std::mutex mutex_;
  std::deque<Card> queue_;
};

}  // namespace mp3ps5

#endif  // REX_PLATFORM_PS5
