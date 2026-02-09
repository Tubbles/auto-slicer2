# Telegram Mini App for Settings UI

## Concept

Replace the inline-keyboard settings flow with a Mini App (WebApp) that opens
as a bottom sheet inside Telegram. Users get native sliders, toggles, and
dropdowns instead of chat-based button presses.

## Layout sketch

```
┌─────────────────────────────────┐
│  ← Auto-Slicer Settings        │  header (auto-themed)
├─────────────────────────────────┤
│  Preset:  [Draft] [Std] [Fine] │  tap to apply
│                                 │
│  ── Quality ──────────────────  │
│  Layer Height          [0.2 ▾]  │  dropdown / slider
│  Wall Line Count       [ 3  ]   │  stepper +/-
│                                 │
│  ── Infill ───────────────────  │
│  Infill Density     ====○ 20%   │  range slider
│  Infill Pattern        [grid ▾] │  dropdown (enum)
│                                 │
│  ── Support ──────────────────  │
│  Support Enable        [  OFF]  │  toggle (bool)
│  Adhesion Type      [skirt ▾]   │
│                                 │
│  ── Speed / Temp ─────────────  │
│  Print Speed        ===○ 50mm/s │
│  Print Temp            [200]    │
│  Bed Temp              [ 60]    │
├─────────────────────────────────┤
│          [ Apply Settings ]     │  MainButton (sticky)
└─────────────────────────────────┘
```

## Control mapping

| setting_type | Control                              |
|--------------|--------------------------------------|
| bool         | Toggle switch                        |
| enum         | `<select>` dropdown from defn.options|
| int          | Stepper or slider (min/max from bounds) |
| float        | Slider with step                     |
| str          | Text input                           |

## Data flow

1. User taps menu button or inline "Open Settings" button → opens Mini App URL.
2. Webapp reads `Telegram.WebApp.initDataUnsafe.user.id` to identify user.
3. User interacts with native HTML controls grouped by setting category.
4. Theming via `Telegram.WebApp.themeParams` (bg_color, text_color, etc.).
5. On "Apply", call `Telegram.WebApp.sendData(JSON.stringify({...}))` (≤4096 bytes).
6. Bot receives `message.web_app_data`, validates via `validate_setting()`, stores in `user_settings`.

## Minimal version (one static HTML file)

- ~200 lines HTML/JS/CSS, host on GitHub Pages or nginx.
- Hardcode the ~10 most common settings with appropriate controls.
- Uses `sendData()` — no server endpoint needed.

## Fuller version (live state)

- Add a small HTTP endpoint (aiohttp) returning current user overrides as JSON.
- Webapp fetches on load so controls reflect current state.
- Could expose full setting registry (all ~673 settings) with search/filter.

## Other low-effort UX improvements to do first

- **Set bot commands via BotFather** (zero code, instant discoverability).
- **Scoped commands** — hide admin commands from regular users via `set_my_commands` in `post_init`.
- **Numeric value picker buttons** — +/- inline keyboard for int/float settings.
- **Search pagination** — Next/Prev buttons that edit the message in place.
- **Pre-slice confirmation** — show active overrides with [Slice now] / [Edit settings] after STL upload.
