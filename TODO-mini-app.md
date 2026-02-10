# Telegram Mini App for Settings UI

## Status: Implemented

Core implementation complete. The Mini App provides a full-screen settings
panel with search, presets, and type-appropriate controls for all 673 settings.

### Done

- [x] `auto_slicer/web_auth.py` — initData HMAC-SHA256 validation
- [x] `auto_slicer/web_api.py` — aiohttp HTTP API (registry, get/post settings)
- [x] `webapp/index.html` — self-contained Mini App frontend
- [x] Config fields: `api_port`, `webapp_url`, `api_base_url`
- [x] `/webapp` command sends keyboard button with WebAppInfo
- [x] `post_init` / `post_shutdown` lifecycle for aiohttp server
- [x] Tests for auth module and API pure helpers

### Remaining work

- [ ] **HTTPS setup** — Telegram requires HTTPS for Mini App URLs. Need a reverse
  proxy (nginx/Caddy/cloudflare tunnel) in front of the aiohttp server.
- [ ] **Deploy `webapp/index.html`** — Host on GitHub Pages or similar static host.
- [ ] **Set `api_port`, `webapp_url`, `api_base_url`** in `config.ini`.
- [ ] **End-to-end testing** — Open the Mini App in Telegram and verify the full flow.
- [ ] **BotFather menu button** — Optionally set a persistent menu button via BotFather
  that opens the Mini App directly (no `/webapp` command needed).

### Nice to have

- Search pagination (virtual scroll for 673 settings)
- Numeric slider for settings with known min/max bounds
- Offline caching of registry data
- Pre-slice confirmation via Mini App
- Scoped bot commands (hide admin commands from regular users)
