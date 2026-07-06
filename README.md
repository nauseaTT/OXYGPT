<div align="center">

# OXYGPT

### A production-grade Telegram assistant for traders — intelligent conversation, market analysis, and content generation in one bot.

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="Telethon" src="https://img.shields.io/badge/Telethon-v2%20(2.0.0a0)-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white">
  <img alt="Google Gemini" src="https://img.shields.io/badge/Google%20Gemini-primary-4285F4?style=for-the-badge&logo=googlegemini&logoColor=white">
  <img alt="OpenAI-compatible" src="https://img.shields.io/badge/OpenAI--compatible-fallback-412991?style=for-the-badge&logo=openai&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-persistence-003B57?style=for-the-badge&logo=sqlite&logoColor=white">
</p>

<p>
  <img alt="Async" src="https://img.shields.io/badge/architecture-async%20first-1f6feb?style=flat-square">
  <img alt="Modular" src="https://img.shields.io/badge/design-modular%20%26%20layered-2ea043?style=flat-square">
  <img alt="Multilingual" src="https://img.shields.io/badge/UI-Persian%20first-e3116c?style=flat-square">
  <img alt="Tools" src="https://img.shields.io/badge/tools-search%20%7C%20image%20%7C%20market%20%7C%20booklet-8957e5?style=flat-square">
</p>

<sub>Multi-provider generative engine · 4 trading-mentor personas · 4 quick-ask skills · tool-calling · multi-window memory · trade journal · smart channel monitoring</sub>

</div>

---

## Table of Contents

<table>
<tr>
<td valign="top">

**Overview**
- [What is OXYGPT?](#what-is-oxygpt)
- [Highlights](#highlights)
- [Feature Matrix](#feature-matrix)
- [At a Glance](#at-a-glance)

**Architecture**
- [System Architecture](#system-architecture)
- [Request Lifecycle](#request-lifecycle)
- [Design Principles](#design-principles)
- [Directory Structure](#directory-structure)

</td>
<td valign="top">

**Operate**
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)

**Capabilities**
- [Generative Engine](#generative-engine)
- [Conversation Model](#conversation-model)
- [The Tool Suite](#the-tool-suite)
- [Mentor Personas](#mentor-personas)
- [Quick-Ask Skills](#quick-ask-skills)

</td>
<td valign="top">

**Depth**
- [Rate Limits & Fair Use](#rate-limits--fair-use)
- [Resilience & Recovery](#resilience--recovery)
- [Admin Console](#admin-console)
- [Optional Modules](#optional-modules)
- [Telethon v2 Foundation](#telethon-v2-foundation)

**Reference**
- [Engineering Notes](#engineering-notes)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

</td>
</tr>
</table>

---

## What is OXYGPT?

**OXYGPT** is a production-grade Telegram bot that puts a multi-provider generative
engine, a suite of live tools, and a set of specialist trading mentors behind a
single, polished, Persian-first chat interface.

It is not a thin wrapper around one model API. It is a **stateful conversation
platform**: every user gets up to five independent conversation windows with
persistent memory and automatic context compression; requests are routed across
a pool of API keys and two provider families with transparent failover; and the
assistant can reach out to the world through tools — searching the web, drawing
market candles, generating images, and rendering shareable HTML booklets — all
narrated back to the user through live, animated progress messages.

Around this core sit two optional, self-contained products: a full **Trade
Journal** for logging, analysing, and publishing trades, and a **Channel
Watcher** that quietly reads Telegram channels and surfaces only what matters,
classified and summarised by an intelligent model.

> **Built on Telethon v2 (`2.0.0a0`).** The entire Telegram layer targets the
> full Telethon v2 rewrite — explicit connect/login, `PeerRef` entities,
> separated events & filters, per-call parse modes, and more. Every Telethon
> symbol is imported through a single in-repo compatibility layer,
> `telethon_compat.py`, keeping the differences in one auditable place. See
> [Telethon v2 Foundation](#telethon-v2-foundation).

---

## Highlights

| | |
|---|---|
| 🧠 **Dual-provider engine** | Google Gemini as the primary brain, any OpenAI-compatible endpoint as fallback — with automatic, transparent failover on retryable errors. |
| 🔑 **16-key round-robin per provider** | Keys rotate automatically to spread rate limits; per-window overrides let specific chats use dedicated services. |
| 🛠️ **Live tool-calling** | Web search, image generation, market OHLC data, and RTL HTML booklet rendering — invoked by the model and streamed back as progress. |
| 🎓 **4 trading mentors** | ICT, Quarterly Theory, Matrix/369, and Price Action — each a distinct analytical persona with its own framework and voice. |
| 🪟 **Multi-window memory** | Five isolated conversation windows per user, persisted in SQLite, with background summarisation that compresses long histories automatically. |
| ⏱️ **12-hour rolling quotas** | Fair-use windows for requests, tokens, images, and files that reset on a Tehran-time schedule, with free and paid tiers. |
| 🛡️ **Self-healing under load** | Sustained `503`s trigger an automatic model downgrade with a scheduled 30-minute recovery, all reported to the admin. |
| 📓 **Trade Journal** | Templated trade entry, rich statistics, model-powered analysis, alerts, and channel publishing — with automatic backups. |
| 📡 **Channel Watcher** | Background monitoring of Telegram channels with importance classification and full-content analysis, delivered as clean summary cards. |
| 🎬 **Animated feedback** | A status animator turns every wait into a live, tool-aware progress message instead of a silent spinner. |

---

## Feature Matrix

<details open>
<summary><b>Conversation & Intelligence</b></summary>

- Dual generative providers (Gemini primary / OpenAI-compatible fallback) with automatic failover
- Multi-key round-robin pools — up to **16 keys per provider**
- Admin-defined multi-service management: independent base URLs, keys, and per-mode models
- Four trading-mentor personas with dedicated analytical frameworks
- Four quick-ask skills: general assistant, Socratic teaching, coding, deep analytical thinking
- Up to **5 independent conversation windows** per user, each with its own history and mode
- Background auto-summarisation when a window grows past **5 messages / 15k tokens**
- Usage-aware auto-downgrade to lighter models under global or per-user pressure

</details>

<details>
<summary><b>Tools & Content</b></summary>

- **Web search** via the model's grounded Google Search, with source attribution
- **Image generation** with eight style presets and content-aware aspect-ratio detection
- **Market data** — OHLC candles for forex & crypto with per-timeframe caching
- **HTML booklets** — sanitised, RTL-optimised, print-friendly branded documents
- Optional local **candlestick charts** (matplotlib, disabled by default)

</details>

<details>
<summary><b>Platform & Operations</b></summary>

- 12-hour rolling usage windows (reset 11:00 / 23:00 Tehran time) with free/paid tiers
- Sustained-`503` auto-downgrade with scheduled 30-minute recovery and admin alerts
- Mandatory channel-join enforcement with cached membership checks
- Full admin console: users, blocks, models, providers, services, locks, dashboards
- Second-verify inactivity detection after 30 minutes of silence
- Inline-query mentor selection and collapsible long messages
- Independent SQLite databases for the bot, trade journal, and channel watcher

</details>

<details>
<summary><b>Optional Modules</b></summary>

- **Trade Journal** — templated entry, search, statistics, model analysis, notifications, auto-backup
- **Channel Watcher** — per-monitor background workers, importance classification, full analysis, PM/group delivery, 90-day retention

</details>

---

## At a Glance

```text
┌─────────────────────────────────────────────────────────────────────────┐
│  User in Telegram                                                         │
│      │  "Analyze EURUSD on the 1h with ICT concepts and draw me a chart"  │
│      ▼                                                                     │
│  ┌───────────────┐   window memory   ┌──────────────────────────────┐    │
│  │  OXYGPT bot   │──────────────────▶│  Generative engine (Gemini)  │    │
│  │  (Telethon 2) │◀──────────────────│  + tool calls + failover     │    │
│  └───────────────┘   live progress   └──────────────┬───────────────┘    │
│      │                                               │                    │
│      │  animated status: "📡 fetching market data…"  │  get_market_data   │
│      ▼                                               ▼                    │
│  Rich reply  ◀── HTML booklet · image · chart · grounded text ── tools    │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## System Architecture

OXYGPT is organised into clean, decoupled layers. The Telegram edge, the
generative engine, the persistence layer, and the tool suite each own a single
responsibility and communicate through narrow interfaces.

```text
telegram.py                     Entry point — loads env, builds TelegramBot, starts the client
│
├── TelegramBot ─ telegram/bot.py
│   │   Orchestrator: sessions, rate limiting, membership checks,
│   │   handler registration, 12-hour reset loop, stale-data cleanup
│   │
│   ├── Event handlers ─ telegram/handlers/
│   │     commands · admin · menu · ai · windows · shortcuts · misc · verify
│   │
│   ├── StatusAnimator ─ telegram/animator.py     Live, tool-aware progress messages
│   │
│   ├── DatabaseManager ─ database.py             SQLite: windows, usage, settings, blocks…
│   │
│   └── AI_Service ─ api_http.py                  Per-user generative session
│         ├── GeminiClientPool      Round-robin Gemini key rotation
│         ├── OpenAIClientPool      Round-robin OpenAI-compatible key rotation
│         ├── ServiceManager        Multi-service provider management
│         └── tools.py              search · image · market · booklet
│
├── Gemini503Manager ─ gemini_503_manager.py      Global 503 detection, downgrade & recovery
│
├── trade_journal/                Optional — logging, stats, analysis, notifications
├── channel_watcher/              Optional — smart-classification channel monitoring
└── chart_generator.py            Optional — matplotlib candlesticks (off by default)
```

### The engine at the center

`AI_Service` (in `api_http.py`) is the heart of the system. Each user's active
window owns one service instance that:

- routes generation to the active provider and **fails over** to the other on retryable errors,
- rotates across the key pool so no single credential absorbs the whole load,
- brokers **tool calls** — each call emits a `ToolEvent` that drives the live progress animation,
- runs **background summarisation** so long conversations stay within budget,
- and isolates Gemini `503`s to the dedicated `Gemini503Manager` rather than a blunt provider swap.

---

## Request Lifecycle

```text
User message
    │
    ▼
Telegram event  ──▶  TelegramBot.pending_message_handler
    │                   ├─ block checks (user / group)
    │                   ├─ mandatory channel-join enforcement
    │                   ├─ rate-limit checks (requests · tokens · images · files)
    │                   ├─ resolve / create AI_Service for the active window
    │                   └─ start StatusAnimator (animated progress)
    ▼
AI_Service.handle_message
    │   append user turn ▸ resolve model ▸ call generate_response(timeout, tool callback)
    ▼
AI_Service.generate_response
    │   route to Gemini or OpenAI-compatible
    │   ├─ retryable error  ▸ fail over to the other provider (session migrates on success)
    │   └─ Gemini 503        ▸ Gemini503Manager (downgrade + scheduled recovery)
    ▼
Provider generates content  ──▶  each tool call emits a ToolEvent ▸ animator updates
    │
    ▼
Response delivery (telegram/handlers/ai.py), by priority:
    0 · HTML booklet  → sent as a document
    1 · Image         → sent with caption
    2 · Chart         → chart image + text
    3 · Plain text    → edits the loading message in place
    │
    ▼
Post-processing
    ├─ persist window session
    ├─ update interaction timestamp (second-verify)
    └─ trigger background summarisation if the window is large enough
```

---

## Design Principles

| Principle | How it shows up in the code |
|---|---|
| **Async-first** | All I/O is `asyncio`; CPU-bound work (chart rendering) is off-loaded with `asyncio.to_thread`. |
| **Graceful degradation** | Provider failover, key rotation, and `503` auto-downgrade keep the bot answering even as parts fail. |
| **Isolation of concerns** | Bot edge, engine, persistence, and tools are separate layers; optional modules use their own databases. |
| **Persistence over memory** | Windows, usage, settings, and blocks live in SQLite so state survives restarts. |
| **Race-safety by design** | `BEGIN IMMEDIATE` guards window creation; version tracking prevents summaries from clobbering new turns. |
| **One place for platform quirks** | Every Telethon v1→v2 difference is absorbed in `telethon_compat.py`, not scattered across the codebase. |

---

## Directory Structure

```text
OXYGPT/
├── telegram.py                 Entry point — builds TelegramBot and starts the client
├── telethon_compat.py          Telethon v1→v2 compatibility layer (import Telethon from here)
├── api_http.py                 Generative engine: providers, tool-calling, summarisation, pools
├── tools.py                    Tools: market data (FCSAPI), image (freegen.app), search, booklets
├── database.py                 SQLite DatabaseManager: windows, usage, settings, blocks, locks…
├── system_prompt.py            Mentor prompts: micheal (ICT), daye (QT), zeussy (369), albrooks (PA)
├── skills.py                   Quick-ask skills: default, learn, coding, deepthink
├── gemini_503_manager.py       Centralised 503 handling & auto-recovery
├── chart_generator.py          Matplotlib candlestick charts (disabled by default)
├── bot_logging.py              Root logging configuration
├── requirements.txt            Python dependencies (pulls Telethon v2 from GitHub)
├── .env.example                Environment template
│
├── telegram/                   Telegram bot package
│   ├── bot.py                  TelegramBot — the orchestrator
│   ├── animator.py             StatusAnimator — animated waiting messages
│   ├── constants.py            ToolEvent, tips, patience messages, phase icons, help text
│   ├── utils.py                Time utilities, HTML truncation, progress bars, UI helpers
│   └── handlers/
│       ├── ai.py               Message-processing pipeline & response delivery
│       ├── admin.py            Admin console: users, blocks, models, providers, services
│       ├── commands.py         Core commands (/start, /cancel, /arise)
│       ├── menu.py             Main menu, help system, callbacks
│       ├── windows.py          Multi-window create / switch / rename / delete
│       ├── shortcuts.py        Quick commands (/ask, /code, /micheal, /w, …)
│       ├── misc.py             Inline query, window panel, switch handlers
│       └── verify.py           Second-verify inactivity detection
│
├── trade_journal/              Optional — trade logging, stats, analysis, notifications
│   ├── database.py             Independent SQLite database
│   ├── handlers/               entry · search · stats · templates · settings · setup · …
│   ├── services/               trade · template · notification · analysis
│   └── ui/                     keyboards & formatters
│
└── channel_watcher/            Optional — smart-classification channel monitoring
    ├── database.py             Independent SQLite database
    ├── handlers/               setup · settings · callbacks
    ├── services/               fetcher · analyzer · notifier
    └── ui/                     keyboards & formatters
```

---

## Requirements

- **Python 3.9+** (3.11+ recommended)
- **Telethon v2 (`2.0.0a0`)** — installed from the official `v2` branch, **not** from PyPI.
  The published `telethon` package is the v1 line and is **not** compatible with this codebase.
- A Telegram **API ID** and **API hash** — from [my.telegram.org](https://my.telegram.org)
- A Telegram **bot token** — from [@BotFather](https://t.me/BotFather)
- At least one **Google Gemini** API key
- *(Optional)* **OpenAI-compatible** API keys for the fallback/secondary provider
- *(Optional)* An **FCSAPI** token for the market-data tool
- *(Optional)* A Telegram **user account** for the Channel Watcher module (first login prompts
  for a code and, if two-step verification is enabled, a password)

---

## Installation

```bash
# 1. Clone
git clone <repo-url>
cd OXYGPT

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate

# 3. Install dependencies (pulls Telethon v2 from GitHub — see note below)
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env with your credentials
```

### Installing Telethon v2

Telethon v2 (`2.0.0a0`) is a full rewrite that has **not** been published to PyPI, so it is
installed straight from the official `v2` branch. This is already wired into `requirements.txt`,
but you can also install it explicitly:

```bash
pip install "git+https://github.com/LonamiWebs/Telethon.git@v2#subdirectory=client"
```

- Do **not** run `pip install telethon` — that installs the incompatible v1 line from PyPI.
- Offline/dev environments can install from a local checkout: `pip install ./telethon-src/client`.
- When Telethon 2.0.0 lands on PyPI, the pin can be replaced with `telethon>=2,<3`.

> **Build note.** The v2 branch generates its TL layer at build time and needs
> `typing_extensions` available during the build. If the wheel build fails with
> `No module named 'typing_extensions'`, install the build helpers first
> (`pip install typing_extensions setuptools wheel`) and retry.

---

## Configuration

Create a `.env` file in the project root:

```env
# ── Telegram credentials ──────────────────────────────
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token

# ── Gemini keys (up to 16; at least one required) ─────
GEMINI_API_KEY_1=your_key_here
GEMINI_API_KEY_2=your_key_here
# … up to GEMINI_API_KEY_16

# ── OpenAI-compatible provider (optional) ─────────────
OPENAI_BASE_URL=https://router.bynara.id/v1
OPENAI_API_KEY_1=
# … up to OPENAI_API_KEY_16

# ── Market data (optional) ────────────────────────────
FCSAPI_TOKEN=your_token_here

# ── Admin (for 503 / system notifications) ────────────
ADMIN_USER_ID=your_telegram_user_id

# ── Channel Watcher user account (optional) ───────────
CW_USER_PHONE=+989123456789   # international format
CW_USER_PASSWORD=             # two-step-verification password; leave empty if 2FA is off
```

> **Login flow (Telethon v2).** The bot account signs in with its token via `connect()` +
> `bot_sign_in(token)`. The Channel Watcher user account signs in via `connect()` +
> `request_login_code()` + `sign_in()` (plus `check_password()` when 2FA is enabled). Because the
> v2 session format differs from v1, existing `bot.session` / `channel_watcher_user.session` files
> are **not** reused — the first v2 run re-authenticates and rewrites them. See
> [Telethon v2 Foundation](#telethon-v2-foundation).

### Runtime settings (changeable via the admin console)

Stored in the SQLite `settings` table and editable live:

| Key | Default | Description |
|-----|---------|-------------|
| `active_provider` | `gemini` | Active provider (`gemini` / `openai`) |
| `quick_ask_model` | `gemini-3.1-flash-lite` | Quick-ask model (Gemini) |
| `mentors_model` | `gemini-3.1-flash-lite` | Mentor model (Gemini) |
| `search_model` | `gemini-2.5-flash` | Web-search tool model |
| `fallback_model` | `gemini-3.1-flash-lite` | Fallback on Gemini errors |
| `search_fallback_model` | `gemini-2.0-flash` | Fallback for the search tool |
| `openai_quick_ask_model` | `mimo-v2.5-free` | Quick-ask model (OpenAI-compatible) |
| `openai_mentors_model` | `mimo-v2.5-free` | Mentor model (OpenAI-compatible) |
| `openai_fallback_model` | `mimo-v2.5-free` | Fallback (OpenAI-compatible) |
| `cw_classifier_model` | `gemini-3.1-flash-lite` | Channel Watcher classifier model |
| `default_openai_service` | *(empty)* | Default service ID for new windows |

---

## Running the Bot

```bash
python telegram.py
```

On start, the bot connects to Telegram, registers every handler, loads the optional modules
(Trade Journal, Channel Watcher), and begins its background loops.

**First-run notes**

1. A `bot.session` cache is created. Under Telethon v2 the format differs from v1, so a v1-era
   session is not reused — the bot re-authenticates with its token once and rewrites the file.
2. SQLite databases (`bot_database.db`, and the module databases) are auto-created with all tables.
3. The Channel Watcher module needs `CW_USER_PHONE`; on first authentication it prompts for a login
   code and, if 2FA is on, a password (set `CW_USER_PASSWORD` for headless runs). The session is
   then cached.
4. Logs are written to `logs/telegram.log`, `logs/api_http.log`, and `logs/tools.log`.

---

## Generative Engine

### Providers

| Provider | SDK | Key format | Role |
|----------|-----|------------|------|
| Google Gemini | `google-genai` (`from google import genai`) | `GEMINI_API_KEY_1..16` | Primary |
| OpenAI-compatible | `openai` | `OPENAI_API_KEY_1..16` | Fallback / alternative |

### Key rotation

Both providers use round-robin client pools. `GeminiClientPool` reads
`GEMINI_API_KEY_1..16`; `OpenAIClientPool` reads `OPENAI_API_KEY_1..16` with the base URL from
`OPENAI_BASE_URL`. A window's optional `service_id` can override the global provider for that window.

### Failover logic

When the active provider returns a **retryable** error (rate limit, `500`/`502`/`504`, timeout),
`generate_response` transparently falls back to the other provider:

1. Primary fails with a retryable error.
2. The alternate provider is attempted.
3. On success, the session migrates to the working provider.
4. If the alternate also fails, the original error is surfaced.

**Exceptions:** Gemini `503`s are handled by the `Gemini503Manager` (auto-downgrade, not failover);
authentication and validation errors never trigger failover.

### Multi-service management

`ServiceManager` (in `api_http.py`) manages multiple OpenAI-compatible services, each with an
independent base URL, its own round-robin key pool, and per-mode models (`search`, `quick_ask`,
`mentors`, `fallback`). Services are stored in the database and administered through the console.

---

## Conversation Model

### Windows

Each user can hold **up to 5 independent conversation windows**, each with:

- its own history (capped at 40 messages),
- its own usage counters,
- a mode (`quick_ask` or `mentor`) and optional `mentor_key`,
- an optional `service_id` (for OpenAI multi-service routing),
- and `last_interaction_time` / `last_user_message` for second-verify.

| Command | Action |
|---------|--------|
| `/w` | List all windows |
| `/sw <id>` | Switch to a window |
| `/new` | Create a new window |
| `/clear` | Clear the active window's history |

Windows are also managed from the UI (**Menu → Manage Windows**).

### Automatic summarisation

When a window passes **5 user messages and 15k+ tokens**, a background task compresses it:

1. Uses `gemini-3.1-flash-lite` (search-pool fallback: `gemini-2.0-flash`).
2. A 30-second cooldown prevents tight spawn loops.
3. Lock-based, so only one summarisation runs at a time.
4. Version-tracked — the summary is only persisted if history is unchanged during the run.
5. Targets ~20% of the current token count as a structured state report.
6. The summary replaces prior history in the database.

---

## The Tool Suite

Four tools are exposed to the models. Timeouts are configurable in `api_http.py`.

| Tool | Function | Backend | Timeout | Purpose |
|------|----------|---------|---------|---------|
| 🔎 Web Search | `search_web` | Gemini Google Search | 30s | Fresh information, with source attribution |
| 🎨 Image Generation | `generate_image` | freegen.app (WebSocket) | 90s | Text-to-image with style presets |
| 📈 Market Data | `get_market_data` | FCSAPI | 20s | OHLC candles (forex / crypto) |
| 📄 HTML Booklet | `generate_html_booklet` | Local rendering | 60s | RTL, print-ready branded documents |

**Web Search** — uses the model's grounded Google Search; grounding metadata (source URLs/titles)
is extracted and surfaced through the animator.

**Image Generation** — styles: `realistic`, `anime`, `cartoon`, `oil_painting`, `digital_art`,
`minimal`, `3d_render`, `watercolor`; aspect ratios `1:1`/`4:3`/`16:9`/`9:16`/`auto`; automatic
prompt enhancement; WebSocket progress callbacks; up to 3 retries; tier-limited.

**Market Data** — FCSAPI (`https://api-v4.fcsapi.com`); `EXCHANGE:TICKER` symbols
(e.g. `FX:EURUSD`, `BINANCE:BTCUSDT`) with automatic prefixing; CSV with ISO-8601 timestamps in
New York time; per-timeframe file cache; up to 3 calls per turn; optional matplotlib chart.

**HTML Booklet** — themes `wood`/`blue`/`green`/`purple`/`dark`/`auto`; sanitised
(script/iframe/object/embed/form removed); RTL-optimised with the Vazirmatn font; branded
header/footer; print-friendly CSS; up to 2 MB content; tier-limited.

---

## Mentor Personas

Four trading mentors, each a fully-realised analytical persona with its own framework, voice, and
tool discipline. Market data is enabled automatically in mentor mode.

| Mentor | Key | Framework | Voice |
|--------|-----|-----------|-------|
| **Micheal Huddleston** | `micheal` | Inner Circle Trader (ICT) | Curt, dense, impatient — kill zones, liquidity, DOL, PD arrays |
| **Jevaunie Daye** | `daye` | Quarterly Theory (QT) | Cold, data-first — time cycles, Q-phases, SSMT, True Opens |
| **Zeussy / Frank369** | `zeussy` | The Matrix Unlocked (369) | Precise, tactical — 369 theory, MMXM, ERL/IRL, Twitter Model |
| **Al Brooks** | `albrooks` | Price Action | Patient, probabilistic — bar-by-bar, Always-In, trading ranges |

Each mentor prompt carries strict Telegram-HTML formatting rules (including an RLM marker for
right-to-left Persian text), its complete framework documentation, tool constraints, and behaviour
anchors.

---

## Quick-Ask Skills

Invoke via `/ask`, `/learn`, `/code`, `/deep` — the general-purpose assistant with a selectable
working style.

| Skill | Key | Behaviour |
|-------|-----|-----------|
| Default | `default` | General assistant — Persian, casual, no extra system prompt |
| Learn | `learn` | Socratic teaching — diagnose first, toolkit-based, one step at a time |
| Coding | `coding` | Senior engineer — clean code, type hints, architecture-first |
| DeepThink | `deepthink` | Analytical reasoning — steelman opposing views, structured, epistemically careful |

---

## Rate Limits & Fair Use

Per-user quotas within each 12-hour window (reset at **11:00** and **23:00** Tehran time):

| Tier | Requests | Tokens | Images | HTML Files |
|------|---------:|-------:|-------:|-----------:|
| **Free** | 25 | 150,000 | 15 | 10 |
| **Paid** | 760 | 300,000 | 40 | 25 |

- **80% soft warning** — shown once per window as limits approach.
- **Hard limit** — blocks further requests until reset, with a live countdown.
- **Group usage** — tracked separately in the `group_usage` table.

### Auto-downgrade thresholds

| Level | Trigger | Effect |
|-------|---------|--------|
| Global | > 1,000 requests **or** > 5M tokens across all users | Non-mentor requests use `gemini-3.1-flash-lite` |
| Per-user | > 300 requests **or** > 2.5M tokens for one user | That user's requests use `gemini-3.1-flash-lite` |

---

## Resilience & Recovery

OXYGPT is built to keep answering while things go wrong around it.

- **Provider failover** — retryable errors migrate the session to the healthy provider mid-flight.
- **Key rotation** — round-robin pools spread load so a throttled key does not stall the bot.
- **`503` self-healing** — the `Gemini503Manager` watches for sustained `503`s: on the second
  occurrence it downgrades all `gemini-*-flash` models to `gemini-3.1-flash-lite` database-wide,
  notifies the admin, and schedules an **automatic 30-minute recovery** that restores the original
  models and resets the counter.
- **Missed-reset recovery** — a startup check applies any 12-hour resets that were missed while the
  bot was offline.
- **Stale-data cleanup** — an hourly loop evicts pending entries older than 24 hours.

---

## Admin Console

Open with `/arise` (admin only).

| Area | Capabilities |
|------|--------------|
| **Users** | List users, view usage, toggle tier (free/paid), reset limits, clear windows |
| **Blocks** | Block/unblock users and groups with reason tracking |
| **Models** | Configure models per mode (quick-ask, mentors, search, fallback) for both providers |
| **Providers** | Switch the global provider between Gemini and OpenAI-compatible |
| **Services** | Create / edit / delete / activate OpenAI-compatible services |
| **Locks** | Add/remove mandatory channel-join locks |
| **Insights** | Usage export, token leaders (users & groups), connection test, live token dashboard |

Admin user IDs are defined in `telegram/bot.py` (`self.admin_ids`).

---

## Optional Modules

### 📓 Trade Journal

A complete trade logging and analysis product, registered automatically on startup via
`trade_journal.register_handlers()`.

- **Templated entry** — live form with custom fields and symbol management
- **Search** — filter trades by date, symbol, outcome, or template
- **Statistics** — win rate, profit factor, drawdown, and Sharpe-like metrics
- **Analysis** — model-powered pattern review of trade history
- **Notifications** — loss-streak alerts, daily-goal tracking, channel publishing
- **Auto-backup** — every 3 hours, retaining the last 5 backups

### 📡 Channel Watcher

Background monitoring of Telegram channels that surfaces only what matters, loaded automatically on
startup and reading channels through a **separate Telethon user client**.

- **Monitor setup** — pick a channel, set a check interval, choose an importance filter
- **Importance classification** — a lightweight classifier decides what is worth analysing
- **Full analysis** — deep content analysis with custom system prompts
- **Delivery** — clean summary cards to a user PM or a group
- **Per-monitor workers** — independent asyncio tasks on a 60-second cycle
- **90-day retention** — old analyses are cleaned up automatically

Requires `CW_USER_PHONE` (and a first-login code, plus `CW_USER_PASSWORD` if the account uses 2FA).

---

## Telethon v2 Foundation

This project runs on **Telethon v2 (`2.0.0a0`)**, the full rewrite from
[LonamiWebs/Telethon@v2](https://github.com/LonamiWebs/Telethon). v2 changes a great deal of surface
API relative to the v1 line on PyPI. Rather than scatter those differences across the codebase, they
are concentrated in one compatibility layer, **`telethon_compat.py`**, which re-exports v2 under
familiar names and monkeypatches a few methods.

> **Golden rule:** import every Telethon symbol from `telethon_compat`, never from `telethon`
> directly. (Notably, `filters` cannot be imported from `telethon` at all in v2.)

### What the compat layer provides

- `TelegramClient` → v2 `Client`
- `events`, `types`, `errors` (any error name resolves), `tl` (the private raw API), and `filters`
- `InlineKeyboard` and a `Button` shim mapping `Button.inline/url/…` onto `types.buttons.*`
- Common error aliases (`ChannelPrivateError`, `UserNotParticipantError`, `MessageNotModifiedError`, …)
- Peer/entity helpers (`strip_channel_mark`, `user_ref`, `channel_ref_from_stored_id`, …)
- Event/filter helpers (`data_regex(...)`, `text_regex(...)`)
- A `typing_action(client, peer)` async context manager (v2 removed `client.action(...)`)
- `photo_dedup_key(file_obj)` for de-duplicating photos
- Thin wrappers for `send_message` / `edit_message` / `send_file` / `delete_messages`

### Key v1 → v2 changes (absorbed by the compat layer)

| Area | v1 | v2 |
|------|----|----|
| Client type | `TelegramClient` | `Client` (re-exported as `TelegramClient`) |
| Startup | `client.start(...)` | explicit `connect()` + `is_authorized()` + `bot_sign_in()` / `request_login_code()` + `sign_in()` |
| Typing | `client.action(peer, "typing")` | `typing_action(client, peer)` context manager |
| Callback event | `events.CallbackQuery(data=/pattern=)` | `events.ButtonCallback` + `filters.Data(...)` / `data_regex(...)` |
| Parse mode | `parse_mode="html"` | per-call `html=` / `caption_html=` |
| Entities | marked IDs, `get_entity` | `PeerRef` model (`UserRef`/`ChannelRef`/`GroupRef`), `resolve_username(...)` |
| Send file | `send_file([...], thumb=)` | single file, no `thumb=`; albums via `prepare_album()` |
| Flood wait | `FloodWaitError.seconds` | `.value` |

Full details live in `MIGRATION_NOTES.md`; every non-trivial migrated line carries an inline
`# v2:` comment.

### Session compatibility (re-login required)

The v2 session format is **not** compatible with v1 files. On the first v2 run, `bot.session` is
rewritten from the bot token automatically, and `channel_watcher_user.session` performs a fresh
login (code + optional 2FA password) before being cached. **Do not delete these session files once
re-created.**

---

## Engineering Notes

### Breaking a circular import

`ToolEvent` is used by both `animator.py` and `api_http.py`. `api_http.py` imports it lazily via
`_get_tool_event_class()` to break the cycle:

```text
api_http → telegram.constants → telegram.__init__ → telegram.bot → api_http
```

### Hardening & race conditions handled

| Concern | Mitigation |
|---------|------------|
| Duplicate window creation | `BEGIN IMMEDIATE` before the COUNT check |
| Tight summarisation loops | 30-second cooldown via `_last_summarize_attempt` |
| Summary overwriting new turns | Version-tracked — persist only if history is unchanged |
| Pending messages never evicted | Hourly cleanup of entries older than 24h |
| Sequential membership checks | `asyncio.gather` for concurrent checks + 300s TTL cache |
| File/usage leaks on error | Increment after validation, decrement / clean up on failure |
| Repeated tool retries | Track a `failed_tool_names` set per iteration |

### Handler registration order

Callback patterns are matched by regex against callback data, so when two patterns share a prefix
the more specific one is registered first (with negative lookaheads where ordering alone is not
enough). Under v2, events and filters are separate objects combined at registration time; regex on
data is expressed through the compat helper `data_regex(...)`:

```python
from telethon_compat import events, data_regex

# more specific pattern first
self.bot.on(events.ButtonCallback, data_regex(r"service_delete_confirm:"))(self.service_delete_confirm)
# less specific uses a negative lookahead
self.bot.on(events.ButtonCallback, data_regex(r"^service_delete:(?!confirm)"))(self.service_delete)
```

### Testing & logging

There is no formal test suite; testing is manual via Telegram. When contributing, exercise both
quick-ask and mentor modes, all four tools, the rate limiter (by exhausting limits), and provider
failover (by revoking keys). The root logger writes to stdout and `telegramAI.log`; module loggers
write to `logs/telegram.log`, `logs/api_http.log`, and `logs/tools.log`.

---

## Troubleshooting

<details>
<summary><b>Bot won't start — <code>TELEGRAM_API_ID/HASH/BOT_TOKEN must be set</code></b></summary>

Ensure a `.env` file exists in the project root with all three variables populated.
</details>

<details>
<summary><b>Wrong Telethon version — <code>cannot import name 'Client' from 'telethon'</code></b></summary>

You almost certainly have the PyPI v1 line installed. Reinstall v2 from the `v2` branch:

```bash
pip uninstall -y telethon
pip install "git+https://github.com/LonamiWebs/Telethon.git@v2#subdirectory=client"
python -c "import telethon; print(telethon.__version__)"   # expect 2.0.0a0
```

If the wheel build fails with `No module named 'typing_extensions'`, install
`typing_extensions setuptools wheel` first, then retry.
</details>

<details>
<summary><b>Account re-requests login after upgrading to v2</b></summary>

Expected. The v2 session format differs from v1, so old session files are not reused. Let the bot
re-authenticate once (bot token for `bot.session`; login code + optional `CW_USER_PASSWORD` for the
Channel Watcher account). Do **not** delete the session files afterwards.
</details>

<details>
<summary><b><code>ModuleNotFoundError: No module named 'google.genai'</code></b></summary>

Run `pip install -r requirements.txt`. `google-genai` (imported as `from google import genai`) is a
core dependency.
</details>

<details>
<summary><b>Market data returns <code>FCSAPI_TOKEN is not configured</code></b></summary>

Set `FCSAPI_TOKEN` in `.env`. Get a token from [fcsapi.com](https://fcsapi.com).
</details>

<details>
<summary><b>Image generation fails with <code>aiohttp is not installed</code></b></summary>

Run `pip install aiohttp aiofiles`.
</details>

<details>
<summary><b>Bot is "online" but silent</b></summary>

Check `logs/telegram.log`; verify the token; confirm the user isn't blocked (admin console) and has
joined every required channel.
</details>

---

## Contributing

1. Fork the repository.
2. Create a change-specific feature branch.
3. Make focused changes with type hints and docstrings.
4. Test manually via Telegram (both modes, all tools, limits, failover).
5. Open a pull request following the existing code style.

See [Engineering Notes](#engineering-notes) for common gotchas — especially the compat-layer import
rule and handler registration order.

---

## License

Internal project. No public license specified.

<div align="center">
<sub>Crafted with care for traders who live in Telegram.</sub>
</div>
