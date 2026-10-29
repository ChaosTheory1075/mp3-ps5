// Max Payne 3 PS5 - its Xbox 360 achievements as PS5-style trophies (from
// gta4-ps5's hub_trophies.h, as Crackdown PS5 ports it): the menu's Trophies
// page (mp3_menu.cpp) and the popup in the game (mp3_trophies_game.cpp).
//
// The achievements are Max Payne 3's 46 (in its default.xex's XDBF: ids 6 to
// 89). The 11 multiplayer ones need Rockstar's online service, which is gone:
// they're listed apart and don't count toward the platinum; the other 35 do.
// The game unlocks them as on the Xbox 360
// and the runtime keeps them in user_data/achievements/5454086B.toml. When
// the game starts, the port also writes their list and pictures there
// (WriteCatalog), so the menu, which opens before the game, can show them.
//
// Grades by points: up to 15 bronze, up to 30 silver, more gold.
#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include "imgui.h"

namespace mp3ps5::trophies {

constexpr uint32_t kTitleId = 0x5454086B;
// (the achievement's flags: its description shows while it's locked; without it, it's a hidden one)
constexpr uint32_t kShowUnachieved = 0x8;

enum class Grade { kBronze, kSilver, kGold, kPlatinum };

Grade GradeFor(uint32_t gamerscore);
const char* GradeName(Grade grade);  // "Bronze"...
// The achievements that count toward the platinum (all but the multiplayer ones).
bool Here(uint32_t id);
// A multiplayer one (Rockstar's online service: not earnable here).
bool Online(uint32_t id);

struct Trophy {
  uint32_t id = 0, gamerscore = 0, flags = 0, image_id = 0;
  std::string name, description, locked_description;
  int64_t earned = 0;  // unix time; 0: not earned
  Grade grade() const { return GradeFor(gamerscore); }
  bool hidden() const { return !(flags & kShowUnachieved); }
  bool online() const { return Online(id); }
};

struct List {
  bool loaded = false;           // the list was there (the game has started once)
  std::vector<Trophy> trophies;  // the game's order (with the multiplayer ones)
  // (the counts below: the ones toward the platinum; the multiplayer ones apart)
  int earnable = 0, earned = 0, online = 0, online_earned = 0;
  int grade_total[3] = {}, grade_earned[3] = {};
  uint32_t points = 0, points_earned = 0;
  int64_t last_earned = 0;
  // The progress, 0-100 (by points, as the game weighs them).
  int Percent() const;
  bool Platinum() const { return earnable > 0 && earned == earnable; }
};

// UserData/achievements and its files.
std::filesystem::path Folder(const std::filesystem::path& user_data);
std::filesystem::path IconFile(const std::filesystem::path& user_data, uint32_t image_id);
std::filesystem::path TitleIconFile(const std::filesystem::path& user_data);

// The list with what's been earned (the runtime's unlocks file).
List Load(const std::filesystem::path& user_data);
// The list's file, written when it changed (the game, as it starts).
bool WriteCatalog(const std::filesystem::path& user_data, const std::vector<Trophy>& trophies);

int64_t UnixTime(uint64_t filetime);

// The popup's card (top right), `age` seconds into its showing: in from the
// right, then away. `display`: the screen's size.
struct Card {
  ImTextureID picture = 0;  // the achievement's (the platinum: the game's); 0: a cup
  std::string name;
  Grade grade = Grade::kBronze;
  bool platinum = false;
  float Seconds() const { return platinum ? 7.0f : 5.5f; }
};
void DrawCard(ImDrawList* dl, ImVec2 display, const Card& card, float age, ImFont* body, ImFont* bold);

// A trophy cup, `height` tall, centred on `c`: in its grade's colours (not
// lit: greyed, for one not earned yet); `on_light`: on a light background.
void DrawCup(ImDrawList* dl, ImVec2 c, float height, Grade grade, float alpha = 1, bool lit = true,
             bool on_light = false);
ImU32 GradeColor(Grade grade, float alpha = 1);

}  // namespace mp3ps5::trophies
