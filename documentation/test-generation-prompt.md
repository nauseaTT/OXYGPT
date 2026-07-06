# پرامپت تولید تست برای پروژه OXYGPT

> این فایل یک **پرامپت آماده** است — آن را به یک دستیار کدنویسِ توانمند (که به مخزن دسترسی دارد) بدهید تا بین **۱۰۰ تا ۲۰۰ تست** برای پروژه بنویسد.
> خودِ تست‌ها اینجا نوشته نشده‌اند؛ فقط پرامپتِ تولیدشان اینجاست.

---

## نحوه استفاده

۱. کل بلوکِ زیر (از خط `You are a senior...` تا انتها) را کپی کنید.
۲. آن را در ابتدای یک نشستِ کاری با دستیار کدنویس بچسبانید — دستیاری که به کل سورس‌کد `OXYGPT` دسترسی خواندن دارد.
۳. اجازه دهید تست‌ها را در مسیر `tests/` تولید کند.

---

## پرامپت (به انگلیسی — چون دستیار کد با انگلیسی دقیق‌تر عمل می‌کند)

```text
You are a senior Python test engineer. Your job is to author a comprehensive,
production-quality automated test suite for the OXYGPT project — a Telegram
assistant bot built on Telethon v2 (2.0.0a0), Google Gemini + OpenAI-compatible
providers, SQLite persistence, a tool-calling layer, and two optional modules
(Trade Journal and Channel Watcher).

════════════════════════════════════════════════════════════════════════
GOAL
════════════════════════════════════════════════════════════════════════
Write between 100 and 200 individual test functions (count each `def test_*`
and each `@pytest.mark.parametrize` case as one test) covering the codebase.
Aim for meaningful coverage of behavior, not vanity count. Distribute tests
roughly according to the testing pyramid: a broad base of fast unit tests, a
smaller band of integration tests, and a thin top of end-to-end/flow tests.

════════════════════════════════════════════════════════════════════════
GROUND RULES (read the code first)
════════════════════════════════════════════════════════════════════════
1. BEFORE writing anything, actually read the modules you are testing. Never
   assume a signature — open the file and confirm the real function/class name,
   parameters, return type, and error behavior. If a symbol does not exist,
   do not test it.
2. Import Telethon symbols ONLY through `telethon_compat` in any test helper —
   never from `telethon` directly (v2 constraint). Prefer mocking the Telegram
   client entirely; do not hit the real Telegram network.
3. NEVER make real network calls, real API calls (Gemini, OpenAI, FCSAPI,
   freegen.app), or touch real session files. Mock/stub every external
   boundary. Tests must run fully offline and be deterministic.
4. NEVER read or mutate the real databases or `.session` files. For anything
   touching `DatabaseManager`, use a temporary SQLite database via a pytest
   fixture (e.g. `tmp_path`) so each test starts from a clean schema.
5. Tests must be isolated and order-independent — no shared mutable global
   state leaking between tests. Reset singletons (e.g. the global
   `Gemini503Manager`) between tests where relevant.
6. Do NOT modify production code to make tests pass. If you find a genuine bug,
   write a test that documents the expected behavior and mark it
   `@pytest.mark.xfail(reason="...")` with a clear explanation instead of
   editing the source.

════════════════════════════════════════════════════════════════════════
TOOLING & CONVENTIONS
════════════════════════════════════════════════════════════════════════
- Framework: pytest. Async tests: pytest-asyncio (use `@pytest.mark.asyncio`).
- Mocking: unittest.mock (Mock, AsyncMock, MagicMock, patch). Use AsyncMock for
  every awaited call (Telegram client methods, provider calls, tool coroutines).
- Structure every test with the Arrange-Act-Assert (AAA) pattern, with a short
  comment marking each section.
- Use fixtures in a `tests/conftest.py` for shared setup: a temp DatabaseManager,
  a fake Telegram client/event, fake provider responses, sample market CSV, etc.
- Use `@pytest.mark.parametrize` to cover many input variations compactly
  (valid/invalid symbols, each style preset, each theme, each mentor key, each
  skill key, boundary values for limits, …).
- One behavior per test. Test names must read as specifications, e.g.
  `test_free_tier_blocks_request_after_25_requests`,
  `test_provider_falls_back_to_openai_on_timeout`,
  `test_summarization_skipped_below_token_threshold`.
- Group tests into files that mirror the source layout (see LAYOUT below).
- Add `pytest.ini`/`pyproject` markers if helpful: `unit`, `integration`, `e2e`.
- Include docstrings on non-obvious tests explaining WHAT behavior is verified.

════════════════════════════════════════════════════════════════════════
WHAT TO COVER (map to the real codebase)
════════════════════════════════════════════════════════════════════════
Read each module and derive concrete cases. Suggested coverage map:

A) database.py — DatabaseManager (aim ~30-45 tests)
   - Schema creation: every table exists after init on a fresh temp DB.
   - Conversation windows: create, cap at 5 per user, switch, rename, delete,
     clear history, history cap at 40 messages, race-safe creation
     (BEGIN IMMEDIATE — a duplicate-create attempt must not exceed the cap).
   - Usage counters: increment requests/tokens/images/html; read back;
     reset logic; free vs paid tier thresholds; group_usage tracked separately.
   - Settings get/set round-trip with defaults (active_provider,
     quick_ask_model, etc.).
   - Blocks: block/unblock user & group, reason stored, is-blocked queries.
   - Locks (mandatory channels): add/remove/list.
   - 503 state: set_503_downgrade, get_503_state, restore/clear,
     downgrade_gemini_models returns the changed map, restore reverses it.
   - Pending states / stale cleanup boundaries (>24h evicted, <24h kept).

B) api_http.py — the generative engine (aim ~20-30 tests)
   - Round-robin key rotation: N calls cycle through the pool deterministically.
   - Provider fallback: on a retryable error (429/500/502/504/timeout) it
     switches to the other provider; on success the session migrates.
   - Auth/validation errors NEVER trigger fallback.
   - Gemini 503 is routed to the 503 manager, NOT to provider fallback.
   - Model resolution from settings vs per-window service_id override.
   - Summarization trigger: fires at >=5 messages AND >=15k tokens; skipped
     below either threshold; 30s cooldown prevents re-entry; version-tracked
     so a summary is discarded if history changed mid-run.
   - ToolEvent lazy import does not raise (circular-import guard).
   - Overall/tool timeouts respected (use fake clock / mocked awaitables).

C) tools.py — the tool suite (aim ~20-30 tests)
   - get_market_data: symbol normalization (adds FX:/BINANCE: prefix),
     EXCHANGE:TICKER parsing, cache hit vs miss, missing FCSAPI_TOKEN error,
     max 3 calls per turn, CSV shape. Mock the HTTP layer.
   - generate_image: each of the 8 style presets maps correctly, aspect-ratio
     detection incl. `auto`, prompt enhancement adds modifiers, retry up to 3,
     missing aiohttp handled. Mock the WebSocket/HTTP.
   - search_web: grounding metadata (urls/titles) extracted; empty results
     handled. Mock the provider.
   - generate_html_booklet: sanitizer strips script/iframe/object/embed/form;
     each theme incl. `auto`; size bounds (<100 chars rejected, >2MB rejected);
     RTL/Vazirmatn scaffolding present.

D) gemini_503_manager.py (aim ~10-15 tests)
   - First 503 returns fallback model, no downgrade yet.
   - Second 503 triggers database-wide downgrade + admin notification + schedules
     recovery (mock asyncio.sleep / freeze time so no real 30-min wait).
   - Already-downgraded state is idempotent (no double downgrade).
   - reset_counter resets the count; recovery restores original models and
     clears state; get_stats reflects current state.
   - Singleton init is idempotent (second init_global_503_manager is a no-op).

E) skills.py & system_prompt.py (aim ~8-12 tests)
   - Every skill key (default/learn/coding/deepthink) exists with name/icon/prompt.
   - Every mentor key (micheal/daye/zeussy/albrooks) exists and returns a prompt.
   - Prompts are non-empty strings; formatting invariants hold if asserted.

F) telegram/ layer — handlers, bot, constants, utils (aim ~20-35 tests)
   - Rate-limit gate: free tier blocked after 25 requests; 80% soft warning
     shown once per window; countdown computed; paid tier higher ceilings.
   - Membership check caching (300s TTL): second call within TTL does not
     re-query; concurrent checks gathered.
   - Response delivery priority order: html booklet > image > chart > text.
   - utils.py pure helpers: HTML truncation, progress bars, time formatting,
     Tehran-time reset boundary math (11:00/23:00), collapsible-message logic.
   - constants.py: ToolEvent construction, presence of TIPS/PATIENCE/PHASE_ICONS.
   - Admin gate: non-admin id rejected, admin id accepted (mock the event).
   Mock the Telegram client/event; assert on the intended side effects
   (which send/edit was called, with what text/keyboard) rather than networking.

G) Optional modules (aim ~15-25 tests)
   - trade_journal: temp DB schema; trade CRUD; template create/apply; stats
     math (win rate, profit factor) on a known fixture dataset; notification
     triggers (loss-streak, daily-goal) fire at the right boundary; album/
     forward parsing helpers.
   - channel_watcher: temp DB schema; importance classification decision on
     mocked classifier output; analyzed-message dedup; 90-day retention cleanup
     boundary; build_user_prompt/sanitize helpers.

════════════════════════════════════════════════════════════════════════
SUGGESTED FILE LAYOUT
════════════════════════════════════════════════════════════════════════
tests/
  conftest.py                     # shared fixtures (temp DB, fake client, samples)
  test_database.py
  test_api_http_providers.py
  test_api_http_summarization.py
  test_tools_market.py
  test_tools_image.py
  test_tools_search.py
  test_tools_booklet.py
  test_gemini_503_manager.py
  test_skills_and_prompts.py
  test_rate_limits.py
  test_membership_and_admin.py
  test_response_delivery.py
  test_utils.py
  test_trade_journal.py
  test_channel_watcher.py

Also add (if missing): a `requirements-dev.txt` with pytest + pytest-asyncio +
(optional) pytest-cov, and a minimal `pytest.ini` enabling asyncio mode and the
custom markers.

════════════════════════════════════════════════════════════════════════
QUALITY BAR
════════════════════════════════════════════════════════════════════════
- Every test must be runnable with a plain `pytest -q` offline, green, and
  deterministic (no flakiness, no sleeps against real time, no network).
- No test depends on another test's side effects.
- Prefer many small, focused tests over few giant ones.
- Where a boundary exists (limits, thresholds, sizes), test just-below,
  exactly-at, and just-above.
- After writing, run the suite, report the final test count and any xfails,
  and summarize coverage by module.

Deliver: the test files, conftest.py, dev requirements, and a short README-style
note in tests/ explaining how to run them (`pip install -r requirements-dev.txt`
then `pytest`).
```

---

## نکته‌ها (فارسی)

- **چرا انگلیسی؟** بدنه اصلی پرامپت به انگلیسی است تا دستیار کد نام‌ها و امضاهای دقیق کد را بدون خطای ترجمه دنبال کند. توضیحات این فایل فارسی است.
- **شمارش تست:** هر `def test_*` و هر حالتِ `parametrize` یک تست شمرده می‌شود؛ به‌سادگی می‌توان به بازه‌ی ۱۰۰ تا ۲۰۰ رسید.
- **آفلاین بودن:** پرامپت صراحتاً تأکید می‌کند هیچ تماس شبکه/API واقعی و هیچ دستکاری دیتابیس یا فایل session واقعی انجام نشود — همه‌چیز mock می‌شود.
- **هرم تست:** توزیع پیشنهادی مطابق هرم تست است (پایه‌ی پهنِ واحد، میانه‌ی یکپارچگی، نوکِ باریکِ سرتاسری).
- **ایمنی سورس:** پرامپت اجازه نمی‌دهد برای سبز کردن تست، کد اصلی دستکاری شود؛ اگر باگ واقعی پیدا شد، با `xfail` مستند می‌شود.

برای درک عمیقِ مفاهیمِ به‌کاررفته در این پرامپت (TDD، AAA، هرم تست، mock و…) فایل آموزشی `documentation/tdd-testing-guide.html` را ببینید.
