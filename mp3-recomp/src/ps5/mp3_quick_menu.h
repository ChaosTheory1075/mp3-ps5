// Max Payne 3 PS5 - the in-game quick menu (Crackdown PS5's): L3 + R3 together (both sticks pressed
// in) opens it over the game, for the cheats, the FPS counter and the DualSense features without
// going back to the app's menu. While it's open the game reads an idle controller; O (or L3 + R3
// again) closes it. The cheats are kept in /app0/cheats.ini, the rest in menu.ini.
//
// The cheats, held each frame from the game's own flags (title update 9's script commands):
//  - god mode: SET_PED_INVINCIBLE's flag (bit 0x80000 of the ped's word at +0x1A8), and the health
//    kept full;
//  - infinite ammo: SET_PED_CONSUMING_AMMO's flag cleared (bit 0x20 of the ped's byte at +0x5C0);
//  - infinite bullet time: the meter kept full (SET_ADRENALINE_AMT 1.0);
//  - one-shot kills: SET_PED_AS_ONE_SHOT_KILL's flag (bit 0x400000 of +0x1804) on every other ped;
//  - enemies ignore you: the "playerignored" developer flag the AI reads (the byte at 8352FEF5).
// Not in multiplayer.
#pragma once

#include <rex/platform.h>
#if REX_PLATFORM_PS5

#include <rex/ui/imgui_dialog.h>

namespace mp3ps5 {

class QuickMenu final : public rex::ui::ImGuiDialog {
 public:
  explicit QuickMenu(rex::ui::ImGuiDrawer* drawer);

 protected:
  void OnDraw(ImGuiIO& io) override;
  // (every vblank only while it's open: drawn with the game's frames otherwise)
  bool WantsContinuousRepaint() const override;

 private:
  int row_ = 0;
  float shown_ = 0;  // 0..1, the fade
};

// Put up over the game, the cheats loaded (Mp3App::OnPostSetup).
void StartQuickMenu(rex::ui::ImGuiDrawer* drawer);

}  // namespace mp3ps5

#endif  // REX_PLATFORM_PS5
