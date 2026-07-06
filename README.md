# OXYGPT — Telegram AI Assistant

A production-grade Telegram bot powered by Google Gemini and OpenAI-compatible APIs, featuring multi-mentor trading personas, tool-calling (web search, image generation, market data, HTML booklets), multi-window conversations, trade journaling, and AI-powered channel monitoring.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Directory Structure](#directory-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Bot](#running-the-bot)
- [Available Scripts](#available-scripts)
- [Development Workflow](#development-workflow)
- [AI Providers](#ai-providers)
- [Conversation Modes](#conversation-modes)
- [Tools](#tools)
- [Mentor Personas](#mentor-personas)
- [Quick Ask Skills](#quick-ask-skills)
- [Rate Limits](#rate-limits)
- [Conversation Windows](#conversation-windows)
- [Conversation Summarization](#conversation-summarization)
- [Admin Panel](#admin-panel)
- [Optional Modules](#optional-modules)
- [Internal Workflow](#internal-workflow)
- [Important Implementation Notes](#important-implementation-notes)
- [Limitations](#limitations)
- [Known Issues](#known-issues)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Dual AI provider support** — Google Gemini (primary) and OpenAI-compatible APIs (fallback/alternative) with automatic provider fallback on error
- **Multi-key round-robin** — Up to 16 API keys per provider, rotated automatically to distribute rate limits
- **Multi-AI-service management** — Admin-defined AI services with independent API keys, base URLs, and model configurations
- **4 trading mentor personas** — Distinct analytical frameworks: ICT (Micheal), Quarterly Theory (Daye), Matrix/369 (Zeussy), Price Action (Al Brooks)
- **4 Quick Ask skills** — General assistant, Socratic teaching, programming, deep analytical thinking
- **Tool calling** — Web search, image generation, market data (OHLC candles), HTML booklet generation
- **Candlestick chart generation** — Local matplotlib-based chart images (opt-in, disabled by default)
- **Multi-window conversations** — Up to 5 independent conversation windows per user
- **Auto-summarization** — Background context compression when conversations exceed 5 messages / 15k tokens
- **12-hour rolling usage windows** — Per-user limits for requests, tokens, images, and HTML files, reset at 11:00/23:00 Tehran time
- **Usage-based auto-downgrade** — Switches to lighter models when global or per-user thresholds are exceeded
- **Gemini 503 auto-recovery** — Automatic model downgrade on sustained 503 errors with scheduled recovery
- **Mandatory channel join enforcement** — Requires users to join specified channels before using the bot
- **Admin panel** — User management, model configuration, block/unblock, provider switching
- **Animated waiting messages** — Context-aware progress animations with tool-specific details
- **Second-verify** — Inactivity detection prompts users to continue or reset after 30 minutes
- **Inline query support** — Quick mentor selection via Telegram inline mode
- **Trade Journal module** — Full trade logging, analysis, statistics, templates, and notifications
- **Channel Watcher module** — AI-powered monitoring of Telegram channels with smart classification
- **Reply-to-Ask** — Stateless `/ask` on replied messages: resolves nested reply chains with sender attribution, zero history persistence
- **Collapsible long messages** — Auto-collapse for lengthy responses with expand/collapse toggle
- **Market data caching** — File-based JSON cache to reduce API calls
- **Separate databases** — Independent SQLite databases for bot, trade journal, and channel watcher

---

## Architecture

### Layered Overview

```
telegram.py                  Entry point — reads env vars, creates TelegramBot, starts client
│
├── TelegramBot (telegram/bot.py)
│   │   Orchestrator: session management, rate limiting, handler registration,
│   │   membership checks, 12-hour reset loop, stale-data cleanup
│   │
│   ├── Event handlers (telegram/handlers/)
│   │   ├── commands.py       — /start, /cancel, /arise
│   │   ├── admin.py          — Admin panel, model config, block/unblock, providers, services
│   │   ├── menu.py           — Main menu, help system, quick ask/mentor/misc callbacks
│   │   ├── ai.py             — Core AI message processing pipeline
│   │   ├── windows.py        — Multi-window creation, switching, rename, delete
│   │   ├── shortcuts.py      — Quick commands (/ask, /code, /micheal, /w, etc.)
│   │   ├── misc.py           — Inline query, window panel, switch handlers
│   │   └── verify.py         — Second-verify inactivity detection
│   │
│   ├── DatabaseManager (database.py)
│   │       SQLite persistence: windows, usage, settings, blocks, locks, services
│   │
│   └── AI_Service (api_http.py)
│           Per-user AI session: provider routing, tool calling, summarization
│           │
│           ├── GeminiClientPool    — Round-robin Gemini key rotation
│           ├── OpenAIClientPool    — Round-robin OpenAI key rotation
│           ├── ServiceManager      — Multi-service provider management
│           └── tools.py            — Tool functions (market data, image, search, HTML)
│
├── trade_journal/              Optional: trade logging, stats, templates, notifications
├── channel_watcher/            Optional: AI channel monitoring with smart classification
└── chart_generator.py          Optional: matplotlib candlestick charts (disabled by default)
```

### Request Flow

```
User message
    │
    ▼
Telegram client receives event
    │
    ▼
TelegramBot.pending_message_handler
    │── Membership / block checks
    │── Rate limit checks (requests, tokens, images, HTML)
    │── Create/get AI_Service for the user's active window
    │── Start StatusAnimator for animated progress
    │
    ▼
AI_Service.handle_message
    │── Append user message to history
    │── Resolve model from DB settings or service config
    │── Call generate_response with timeout & tool-call callback
    │
    ▼
AI_Service.generate_response
    │── Route to Gemini or OpenAI based on active provider
    │── On provider error: auto-fallback to alternate provider
    │── On 503 (Gemini): integrated 503 manager handles downgrade
    │
    ▼
Provider generates content (with optional tool calls)
    │── Each tool call emits a ToolEvent → StatusAnimator update
    │
    ▼
_response (telegram/handlers/ai.py)
    │── Priority 0: HTML booklet → send as document
    │── Priority 1: Image → send with caption
    │── Priority 2: Chart → send chart + text reply
    │── Priority 3: Plain text → edit loading message
    │
    ▼
Background tasks triggered:
    ├── _background_summarize() — if history exceeds threshold
    └── update_window_interaction() — for second-verify timestamp
```

### Key Design Decisions

- **Multi-key rotation** — Up to 16 API keys per provider rotated via round-robin to distribute rate limits
- **Provider fallback** — On retryable errors (429, 500, 502, 504, timeout), automatically falls back to the other provider; on success, session is migrated to the working provider
- **503 isolation** — Gemini 503 errors are handled by `Gemini503Manager` (auto-downgrade + 30-min recovery), NOT by provider-wide fallback
- **12-hour rolling windows** — Usage metrics reset at 11:00 and 23:00 Tehran time. A startup check handles missed resets (e.g., bot was offline)
- **Two-level downgrade** — Global (>1000 req / 5M tokens) enables flash-lite for non-mentor; per-user (>300 req / 2.5M tokens) enables flash-lite for that user
- **Auto-summarization** — Triggered at 5+ messages and 15k+ tokens; uses a lighter model (`gemini-3.1-flash-lite`) via the search pool; 30-second cooldown prevents tight spawn loops
- **Separate SQLite databases** — `bot_database.db` (main), `trade_journal/journal.db`, `channel_watcher/channel_watcher.db`
- **Race-safe window creation** — Uses `BEGIN IMMEDIATE` to prevent duplicate window creation from concurrent requests

---

## Directory Structure

```
AI_TelegramBot/
├── telegram.py                 # Entry point — creates TelegramBot and starts the client
├── api_http.py                 # AI service layer: Gemini/OpenAI providers, tool calling,
│                               # summarization, client pools, service manager
├── tools.py                    # Tool implementations: market data (FCSAPI), image generation
│                               # (freegen.app), web search (Gemini), HTML booklets
├── database.py                 # SQLite DatabaseManager: windows, usage, settings, blocks,
│                               # locks, AI services, 503 state
├── system_prompt.py            # Mentor system prompts: micheal (ICT), daye (QT),
│                               # zeussy (Matrix/369), albrooks (Price Action)
├── skills.py                   # Quick Ask skills: default, learn, coding, deepthink
├── chart_generator.py          # Matplotlib candlestick chart generator (disabled by default)
├── gemini_503_manager.py       # Centralized 503 error handling and auto-recovery
├── bot_logging.py              # Root logging configuration
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (not tracked in git)
├── .gitignore
│
├── telegram/                   # Telegram bot package
│   ├── __init__.py             # Re-exports TelegramBot
│   ├── bot.py                  # TelegramBot class — main orchestrator
│   ├── animator.py             # StatusAnimator — animated waiting messages
│   ├── constants.py            # ToolEvent, tips, patience messages, phase icons, greetings
│   ├── utils.py                # Time utilities, HTML truncation, progress bars, UI helpers
│   └── handlers/               # Event handler modules
│       ├── __init__.py         # Re-exports all handlers
│       ├── ai.py               # AI message processing pipeline, _send_response
│       ├── admin.py            # Admin panel, user/group management, model config
│       ├── commands.py         # Core commands (/start, /cancel, /arise)
│       ├── menu.py             # Main menu, help system, callback handling
│       ├── windows.py          # Multi-window CRUD
│       ├── shortcuts.py        # Quick commands and skill handlers
│       ├── misc.py             # Inline query, panel, switch handlers
│       └── verify.py           # Second-verify inactivity detection
│
├── trade_journal/              # Trade Journal module (optional)
│   ├── __init__.py             # Module registration, auto-backup loop
│   ├── database.py             # Separate SQLite database for trade data
│   ├── states.py               # Conversation state management
│   ├── utils.py                # Formatting and parsing helpers
│   ├── handlers/               # Trade journal event handlers
│   │   ├── entry.py            # Trade entry flow
│   │   ├── search.py           # Trade search
│   │   ├── stats.py            # Statistics and analytics
│   │   ├── template_builder.py # Template CRUD
│   │   ├── settings.py         # Journal settings
│   │   ├── setup.py            # Initial setup
│   │   ├── live_form.py        # Live form handling
│   │   ├── symbols.py          # Symbol management
│   │   ├── bulk.py             # Bulk operations
│   │   └── notifications.py    # Notification management
│   ├── services/               # Business logic services
│   │   ├── trade_service.py    # Trade CRUD
│   │   ├── template_service.py # Template management
│   │   ├── notification_service.py  # Loss streak / goal alerts
│   │   └── ai_analysis.py      # AI-powered trade analysis
│   └── ui/                     # UI components (keyboards, formatters)
│
└── channel_watcher/            # Channel Watcher module (optional)
    ├── __init__.py             # Module registration, monitor workers, analysis cycle
    ├── database.py             # Separate SQLite database for monitors
    ├── states.py               # Conversation state management
    ├── handlers/               # Channel watcher event handlers
    │   ├── setup.py            # Monitor creation/configuration
    │   ├── settings.py         # Monitor settings
    │   └── callbacks.py        # Callback handlers
    ├── services/               # Business logic services
    │   ├── fetcher.py          # Channel message fetching
    │   ├── analyzer.py         # AI classification and analysis
    │   └── notifier.py         # Result delivery/notification
    └── ui/                     # UI components (keyboards, formatters)
```

---

## Requirements

- Python 3.9+
- A Telegram API ID and hash (from [my.telegram.org](https://my.telegram.org))
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- At least one Google Gemini API key
- (Optional) OpenAI-compatible API keys for fallback/secondary provider
- (Optional) FCSAPI token for market data
- (Optional) A Telegram user account for Channel Watcher module

---

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd AI_TelegramBot

# Create and activate a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env   # if available, or create .env manually
# Edit .env with your credentials
```

---

## Configuration

### Environment Variables

Create a `.env` file in the project root with the following:

```env
# ── Telegram Credentials ──
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token

# ── Gemini API Keys (up to 16, at least one required) ──
GEMINI_API_KEY_1=your_key_here
GEMINI_API_KEY_2=your_key_here
# ... up to GEMINI_API_KEY_16

# ── OpenAI-Compatible Provider (optional) ──
OPENAI_BASE_URL=https://router.bynara.id/v1
OPENAI_API_KEY_1=
# ... up to OPENAI_API_KEY_16

# ── FCSAPI Token (optional, for market data tool) ──
FCSAPI_TOKEN=your_token_here

# ── Admin User ID (for 503 notifications) ──
ADMIN_USER_ID=your_telegram_user_id

# ── Channel Watcher User Phone (optional) ──
CW_USER_PHONE=+989123456789
```

### Configurable Settings (via Admin Panel or Database)

Many settings are stored in the SQLite database `settings` table and can be changed at runtime through the admin panel:

| Key | Default | Description |
|-----|---------|-------------|
| `active_provider` | `gemini` | Active AI provider (`gemini` or `openai`) |
| `quick_ask_model` | `gemini-3.1-flash-lite` | Model for Quick Ask mode (Gemini) |
| `mentors_model` | `gemini-3.1-flash-lite` | Model for mentor conversations (Gemini) |
| `search_model` | `gemini-2.5-flash` | Model for web search tool |
| `fallback_model` | `gemini-3.1-flash-lite` | Fallback model for Gemini errors |
| `search_fallback_model` | `gemini-2.0-flash` | Fallback model for search tool |
| `openai_quick_ask_model` | `mimo-v2.5-free` | Model for Quick Ask mode (OpenAI) |
| `openai_mentors_model` | `mimo-v2.5-free` | Model for mentor conversations (OpenAI) |
| `openai_fallback_model` | `mimo-v2.5-free` | Fallback model for OpenAI errors |
| `cw_classifier_model` | `gemini-3.1-flash-lite` | Model for Channel Watcher classifier |
| `default_openai_service` | (empty) | Default OpenAI service ID for new windows |

---

## Running the Bot

```bash
python telegram.py
```

The bot starts, connects to Telegram, registers all event handlers, loads optional modules (Trade Journal, Channel Watcher), and begins the 12-hour reset loop.

### Important First-Time Notes

1. On first run, a `bot.session` file is created (Telethon session cache)
2. SQLite databases (`bot_database.db`, etc.) are auto-created with all required tables
3. The Channel Watcher module requires a user account phone number (`CW_USER_PHONE`) — on first load it prompts for a Telegram verification code
4. Logs are written to `logs/telegram.log`, `logs/api_http.log`, `logs/tools.log`

---

## Available Scripts

| Command | Purpose |
|---------|---------|
| `python telegram.py` | Start the bot |
| `pip install -r requirements.txt` | Install dependencies |

---

## Development Workflow

### Code Style

- Type hints are used throughout — add them to new code
- Docstrings on all public functions and classes (Google-style or plain descriptive)
- Persian comments for internal logic, English for API-level documentation
- Async-first: use `asyncio` for I/O-bound operations, `asyncio.to_thread` for CPU-bound work (e.g., chart generation)

### Testing

There is no formal test suite. Manual testing via Telegram is the current approach. When contributing:
- Test conversations in both Quick Ask and Mentor modes
- Test all four tools (search, image, market, HTML)
- Test rate limiting by exhausting limits
- Test provider fallback by revoking keys

### Logging

- Root logger writes to `telegramAI.log` and stdout (configured in `bot_logging.py`)
- Module-specific loggers write to `logs/`:
  - `logs/telegram.log` — Telegram bot events
  - `logs/api_http.log` — AI service operations
  - `logs/tools.log` — Tool execution logs
- Log level: INFO by default, DEBUG in `bot_logging.py`

---

## AI Providers

### Supported Providers

| Provider | Library | Key Format | Purpose |
|----------|---------|------------|---------|
| Google Gemini | `google-generativeai` | `GEMINI_API_KEY_1`..`16` | Primary provider |
| OpenAI-compatible | `openai` | `OPENAI_API_KEY_1`..`16` | Fallback/alternative |

### Key Rotation

Both providers use round-robin client pools:

- `GeminiClientPool` — reads `GEMINI_API_KEY_1` through `GEMINI_API_KEY_16` from environment
- `OpenAIApiPool` — reads `OPENAI_API_KEY_1` through `OPENAI_API_KEY_16`, URL from `OPENAI_BASE_URL`
- Per-window `service_id` can override the global provider for specific windows

### Provider Fallback Logic

When a provider returns a retryable error (rate limit, server error, timeout), `generate_response` automatically falls back to the other provider:

1. Primary provider fails with retryable error
2. Attempt fallback to the other provider
3. If fallback succeeds: session provider is updated to the working provider
4. If fallback also fails: the original error is raised

**Exceptions:**
- 503 errors on Gemini are handled by `Gemini503Manager` (auto-downgrade, not provider fallback)
- Authentication and validation errors never trigger fallback

### Multi-Service Management

The `ServiceManager` (api_http.py) manages multiple OpenAI-compatible services, each with:
- Independent base URL
- Independent API keys (round-robin)
- Independent model configuration per mode (`search`, `quick_ask`, `mentors`, `fallback`)

Services are stored in the database and managed through the admin panel.

---

## Conversation Modes

### Quick Ask (`/ask`)

General-purpose AI assistant with configurable skills:
- `/ask` — Default assistant (Persian, casual)
- `/learn` — Socratic teaching method
- `/code` — Programming assistant
- `/deep` — Deep analytical thinking

#### Reply-to-Ask (`/ask` on a replied message)

When `/ask` is used as a **reply** to an existing message (with or without additional inline text), the bot enters a **stateless** one-shot analysis mode:

1. **Reply chain resolution** — Walks up to 5 levels of nested replies, collecting text and the first image from each level.
2. **Sender attribution** — Each quoted block is prefixed with the original sender's @username and display name so the model can distinguish between speakers:
   ```
   «««@username (Display Name): message text»»»
   ```
3. **Stateless processing** — A fresh `AI_Service` is created with no window binding and no persisted history. The model receives a special system suffix instructing it to answer in a general/third-person style.
4. **Usage tracking** — Requests still count against the user's quick-ask limit pool.
5. **No retry button** — On failure, the user is asked to retype `/ask`.

**Note:** If the replied message has no text or image content (sticker, voice, deleted message, etc.), the bot notifies the user instead of sending a blank query to the model.

### Mentor Mode (`/micheal`, `/daye`, `/zeussy`, `/albrooks`)

Specialized trading analysis personas. Each mentor has a distinct analytical framework and system prompt. Market data tool is automatically enabled.

---

## Tools

Four tools are exposed to the AI models. Timeouts are configurable in `api_http.py`:

| Tool | Function | External API | Timeout | Description |
|------|----------|-------------|---------|-------------|
| Web Search | `search_web` | Gemini Google Search | 30s | Current information retrieval |
| Image Generation | `generate_image` | freegen.app | 90s | Text-to-image with style presets |
| Market Data | `get_market_data` | FCSAPI | 20s | OHLC candle data (forex/crypto) |
| HTML Booklet | `generate_html_booklet` | Local (file generation) | 60s | RTL HTML documents with themes |

### Web Search

Uses Gemini's built-in Google Search tool. Grounding metadata (source URLs/titles) is extracted and passed to the animator for display.

### Image Generation

- Styles: `realistic`, `anime`, `cartoon`, `oil_painting`, `digital_art`, `minimal`, `3d_render`, `watercolor`
- Aspect ratios: `1:1`, `4:3`, `16:9`, `9:16`, `auto` (content-aware detection)
- Prompt enhancement: automatically adds quality/style modifiers
- WebSocket-based progress reporting with real-time callbacks
- Max 3 retries per generation
- Rate limited per subscription tier

### Market Data

- Source: FCSAPI (`https://api-v4.fcsapi.com`)
- Symbols: `FX:EURUSD`, `BINANCE:BTCUSDT`, etc. (EXCHANGE:TICKER format)
- Auto-formatting: adds `FX:` or `BINANCE:` prefix if missing
- Returns CSV with ISO 8601 timestamps in New York time
- File-based caching with per-timeframe TTL
- Chart generation: optionally generates a candlestick chart via matplotlib (disabled by default)
- Max 3 calls per conversation turn

### HTML Booklet

- Themes: `wood`, `blue`, `green`, `purple`, `dark`, `auto` (content-based)
- Sanitizes HTML (removes script/iframe/object/embed/form tags)
- RTL-optimized with Vazirmatn font
- OXYGPT branded header and footer
- Print-friendly CSS
- Max 2MB HTML content, min 100 chars
- Rate limited per subscription tier

---

## Mentor Personas

| Mentor | Key | Framework | Style |
|--------|-----|-----------|-------|
| Micheal Huddleston | `micheal` | Inner Circle Trader (ICT) | Curt, dense, impatient. Kill zones, liquidity, DOL, PD arrays |
| Jevaunie Daye | `daye` | Quarterly Theory (QT) | Cold, data-first. Time cycles, Q-phases, SSMT, True Opens |
| Zeussy / Frank369 | `zeussy` | The Matrix Unlocked (369) | Precise, tactical. 369 theory, MMXM, ERL/IRL, Twitter Model |
| Al Brooks | `albrooks` | Price Action | Patient, probabilistic. Bar-by-bar, Always In, trading ranges |

Each mentor prompt contains:
- Strict formatting rules (Telegram HTML only, RLM marker for Persian text)
- Complete analytical framework documentation
- Tool usage instructions and constraints
- Behavior guidelines and memory anchors

---

## Quick Ask Skills

| Skill | Key | Behavior |
|-------|-----|----------|
| Default | `default` | General assistant (no additional system prompt) |
| Learn | `learn` | Socratic teaching: diagnose first, toolkit-based instruction, one step forward |
| Coding | `coding` | Senior software engineer: clean code, type hints, architecture-first |
| DeepThink | `deepthink` | Analytical reasoning: steelman opposing views, epistemic clarity, structured analysis |

---

## Rate Limits

Per-user limits within each 12-hour window (reset at 11:00 and 23:00 Tehran time):

| Tier | Requests | Tokens | Images | HTML Files |
|------|----------|--------|--------|------------|
| Free | 25 | 150,000 | 15 | 10 |
| Paid | 760 | 300,000 | 40 | 25 |

- **80% soft warning** — Shown once per reset window when approaching limits
- **Hard limit** — Blocks further requests until reset
- **Countdown display** — Shows remaining time until next reset
- **Group usage** — Tracked separately via `group_usage` table

### Auto-Downgrade Thresholds

| Level | Condition | Action |
|-------|-----------|--------|
| Global | >1000 requests OR >5M tokens across all users | Non-mentor requests use `gemini-3.1-flash-lite` |
| Per-user | >300 requests OR >2.5M tokens for that user | That user's requests use `gemini-3.1-flash-lite` |

---

## Conversation Windows

Each user can have up to 5 independent conversation windows. Each window has:
- Independent history (capped at 40 messages)
- Independent usage counters
- Mode (`quick_ask` or `mentor`)
- Optional `mentor_key`
- Optional `service_id` (for OpenAI multi-service routing)
- `last_interaction_time` and `last_user_message` (for second-verify)

### Window Commands

| Command | Description |
|---------|-------------|
| `/w` | List all windows |
| `/sw <id>` | Switch to a specific window |
| `/new` | Create a new window |
| `/clear` | Clear history in the active window |

Windows are managed through the UI menu (Menu → Manage Windows).

---

## Conversation Summarization

When a conversation exceeds 5 user messages and 15k+ total tokens, a background summarization task is triggered:

1. Uses `gemini-3.1-flash-lite` (fallback: `gemini-2.0-flash`)
2. 30-second cooldown between attempts to prevent tight spawn loops
3. Lock-based: prevents concurrent summarizations
4. Version-tracked: only persists if history hasn't changed during summarization
5. Target size: 20% of current token count, compressed into a structured state report
6. Summary replaces all history in the database

---

## Admin Panel

Access via the `/arise` command (admin-only).

### Available Controls

| Feature | Description |
|---------|-------------|
| User Management | List users, view usage, toggle subscription (free/paid), reset limits, clear windows |
| Block Management | Block/unblock users and groups with reason tracking |
| Model Configuration | Change models per mode (quick_ask, mentors, search, fallback) for both providers |
| Provider Switching | Toggle between Gemini and OpenAI-compatible providers |
| Service Management | Create/edit/delete/activate AI services with independent configs |
| Lock Management | Add/remove mandatory channel join locks |
| Data Export | Export aggregated usage data |
| Token Leaders | Top users/groups by token consumption |
| Connection Test | Test provider connectivity |
| Token Dashboard | Real-time token usage per provider |

Admin user IDs are hardcoded in `bot.py` (`self.admin_ids: List[int] = [8071301975]`).

---

## Optional Modules

### Trade Journal

A full-featured trade logging and analysis system:

- **Trade entry** — Live form with templates, custom fields, symbol management
- **Search** — Filter trades by date, symbol, outcome, template
- **Statistics** — Win rate, profit factor, drawdown, Sharpe-like metrics
- **Templates** — Custom field templates for consistent data entry
- **AI analysis** — Gemini-powered pattern analysis of trade history
- **Notifications** — Loss streak alerts, daily goal tracking, channel publishing
- **Auto-backup** — Every 3 hours, keeps last 5 backups

Loaded automatically on startup via `trade_journal.register_handlers()`.

### Channel Watcher

AI-powered Telegram channel monitoring:

- **Monitor setup** — Select a channel, set check interval, configure importance filter
- **Importance classification** — Uses Gemini classifier to filter messages by importance
- **AI analysis** — Full content analysis with custom system prompts
- **Delivery** — Summary cards sent to user's PM or a group
- **Per-monitor background workers** — Independent asyncio tasks with 60-second check cycles
- **90-day retention** — Automatic cleanup of old analyses
- **User session** — Uses a separate Telethon user client for channel reading

Loaded automatically on startup. Requires `CW_USER_PHONE` for the user client.

---

## Internal Workflow

### Startup Sequence

1. `telegram.py` reads environment variables, creates `TelegramBot` instance
2. `TelegramBot.__init__()`:
   - Initializes SQLite database (`DatabaseManager`)
   - Creates Telethon `TelegramClient`
   - Initializes data structures (sessions dict, sets, caches)
   - Initializes `Gemini503Manager` singleton
   - Binds and registers all event handlers
3. `TelegramBot.run()`:
   - Checks for missed 12-hour resets
   - Starts Telegram client
   - Loads Trade Journal module
   - Loads Channel Watcher module
   - Starts background tasks: `_daily_reset_loop`, `_cleanup_stale_data_loop`
   - Runs until disconnected

### Message Processing Pipeline

1. **Event received** → `pending_message_handler` (catch-all)
2. **Pre-checks**:
   - User blocked? → reject
   - Group blocked? → reject
   - Mandatory channels joined? → warning with join buttons
   - Rate limits exceeded? → error with countdown
3. **Session setup**:
   - Get/create `AI_Service` for the user's active window
   - Start `StatusAnimator` for animated progress
4. **AI processing**:
   - `AI_Service.handle_message()` → appends user message to history
   - Resolves model from DB settings or service config
   - Calls `generate_response()` with tool-call callback
   - Individual tool calls: emit `ToolEvent` → animator updates progress
   - Provider fallback: automatic on retryable errors
5. **Response delivery** (`_send_response`):
   - Priority 0: HTML booklet → send as document
   - Priority 1: Image → send with caption
   - Priority 2: Chart → send chart + text reply
   - Priority 3: Text → edit loading message
6. **Post-processing**:
   - Save window session to database
   - Update interaction timestamp (for second-verify)
   - Trigger background summarization if needed

---

## Important Implementation Notes

### Circular Import Mitigation

`ToolEvent` (used by both `animator.py` and `api_http.py`) is lazily imported in `api_http.py` via `_get_tool_event_class()` to break the circular dependency chain:

```
api_http → telegram.constants → telegram.__init__ → telegram.bot → api_http
```

### Race Conditions Mitigated

| Bug | Fix |
|-----|-----|
| #1 — Duplicate window creation | `BEGIN IMMEDIATE` before COUNT check |
| #10 — Tight summarization spawn loops | 30-second cooldown via `_last_summarize_attempt` |
| #21 — Summary overwrites new messages | Version-tracked: only persist if history unchanged |
| #25 — Pending messages never evicted | Hourly cleanup loop for entries >24h old |
| #32 — Sequential membership checks | `asyncio.gather` for concurrent checks |
| #33 — No membership caching | 300-second TTL cache |
| #31 — HTML usage leak on error | Increment after validation, decrement on error |
| #26 — None message content from providers | `message.content or ""` fallback |
| #12 — Repeated tool retries after failure | Track `failed_tool_names` set per iteration |
| #6 — Pending files not cleaned up | `_cleanup_pending_files` on exception |
| #8 — Empty image-only messages in history | Remove entire message instead of inserting `[{"text": ""}]` |
| #13 — Persistent failure backoff | Reset failure counter on `MessageNotModifiedError` |
| #27 — Duplicate chart generation | Chart generation only in wrapper, not in tools.py |

### Handler Registration Order

Telethon uses `re.match()` for callback patterns. When two patterns share a prefix (e.g., `service_delete:` and `service_delete_confirm:`), the more specific pattern must be registered first. Negative lookaheads are used where ordering is insufficient:

```python
# service_delete_confirm registered FIRST
self.bot.on(events.CallbackQuery(pattern=r"service_delete_confirm:"))(self.service_delete_confirm)
# service_delete uses negative lookahead
self.bot.on(events.CallbackQuery(pattern=r"^service_delete:(?!confirm)"))(self.service_delete)
```

---

## Limitations

- **No formal test suite** — Testing is manual via Telegram
- **Chart generation disabled by default** — Requires matplotlib/pandas; toggle `ENABLE_CHART_GENERATION = True` in `chart_generator.py`
- **Single admin ID hardcoded** — Admin list is in `bot.py` line 112
- **Trade Journal backup hardcoded** — Admin user ID for backups is hardcoded in `trade_journal/__init__.py`
- **No webhook support** — Uses Telethon polling only (long-polling)
- **Channel Watcher requires a user account** — Cannot read channels with bot tokens alone
- **Market data limited to forex/crypto** — FCSAPI provides only these categories
- **Persian-first UI** — Bot UI and AI responses are primarily in Persian

---

## Known Issues

- Image generation API (`freegen.app`) can be unreliable during high demand
- FCSAPI has inherent data delays per timeframe (1m/5m real-time, 1D ~6h delay)
- OpenAI-compatible providers may return `None` for `message.content` in tool-call-only responses
- Channel Watcher user client may fail to start if Telegram sends a code confirmation that requires interactive input

---

## Troubleshooting

### Bot won't start

```
RuntimeError: TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_BOT_TOKEN must be set
```

**Solution:** Ensure `.env` file exists in the project root with all three variables set.

### Dependency issues

```
ModuleNotFoundError: No module named 'google.genai'
```

**Solution:** Run `pip install -r requirements.txt`. The `google-generativeai` library is a core dependency.

### Market data tool returns errors

```
Error: FCSAPI_TOKEN is not configured
```

**Solution:** Set `FCSAPI_TOKEN` in your `.env` file. Get a token from [fcsapi.com](https://fcsapi.com).

### Image generation fails

```
Error: aiohttp is not installed
```

**Solution:** Run `pip install aiohttp aiofiles`. These are optional dependencies for image generation.

### "Bot is online" but no responses

1. Check `logs/telegram.log` for errors
2. Ensure the bot token is valid
3. Verify the user hasn't been blocked (admin panel)
4. Check if the user has joined all required channels

### Provider fallback loops

If a provider keeps failing and the bot falls back repeatedly:
- Check `logs/api_http.log` for error patterns
- Use `/arise` → Provider settings to switch the global provider manually
- Reset failing keys in the admin panel

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with type hints and docstrings
4. Test manually via Telegram
5. Submit a pull request

Please follow the existing code style and patterns. Check the [Important Implementation Notes](#important-implementation-notes) section for common gotchas.

---

## License

Internal project. No public license specified.
