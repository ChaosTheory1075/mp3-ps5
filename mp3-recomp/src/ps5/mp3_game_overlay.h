// Max Payne 3 PS5 - what the app adds over the game while it runs (from
// gta4-ps5's game/ps5_overlay.h):
//
//  - the frame rate counter (SETTINGS > FPS COUNTER: simple, frames a second and
//    the frame time; detailed, also the slowest frames and a graph), in the
//    top-left corner, which the game's HUD leaves empty;
//  - the DualSense's features, from the game's state each frame: the light bar
//    follows Max's health (red when it runs low, a flash when he's hit, green
//    for a painkiller, blue in bullet time), the adaptive triggers (L2's
//    resistance while aiming, R2's trigger pull and a kick for each shot) and
//    added rumble for shots and hits, over the game's own (rex::input::sony,
//    which the PS5 controller driver sends).
#pragma once

#include <rex/platform.h>
#if REX_PLATFORM_PS5

#include <cstdint>

#include <rex/ui/imgui_dialog.h>

namespace mp3ps5 {

class GameOverlay final : public rex::ui::ImGuiDialog {
 public:
  explicit GameOverlay(rex::ui::ImGuiDrawer* drawer);

 protected:
  void OnDraw(ImGuiIO& io) override;
  // (drawn with each game frame, as gta4-ps5's: repainting every vblank queues
  // repeats ahead of the game's frames)
  bool WantsContinuousRepaint() const override { return false; }

 private:
  void Sample();
  void Feedback();

  // the counter: averaged over half a second
  uint64_t window_frames_ = 0;
  int64_t window_start_ns_ = 0;
  float fps_ = 0, ms_ = 0, low_fps_ = 0, worst_ms_ = 0;

  // the DualSense: what the last frame saw
  uint32_t ped_ = 0;
  float health_ = 0, adrenaline_ = 0;
  bool shooting_ = false;
  uint64_t shot_ms_ = 0, hit_until_ms_ = 0, heal_until_ms_ = 0, bullet_time_until_ms_ = 0;
};

// Put up over the game (Mp3App::OnPostSetup).
void StartGameOverlay(rex::ui::ImGuiDrawer* drawer);

}  // namespace mp3ps5

#endif  // REX_PLATFORM_PS5
