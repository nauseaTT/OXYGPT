import json
import logging
from typing import Any, Optional, TYPE_CHECKING
# v2: `from telethon import Button` -> compat Button facade over the v2
# button classes (types.Button.Callback/Url/...). See telethon_compat.py.
from telethon_compat import Button

if TYPE_CHECKING:
    from ..bot import TelegramBot

tlogger = logging.getLogger("telegram")


async def admin_panel(self: "TelegramBot", event: Any) -> None:
    """Show the main admin dashboard.

    This is the top-level destination for the "🔙 بازگشت به پنل ادمین"
    button present on most sub-panels.  ``delete_pending_state`` is
    called as a safety net: if the admin navigated here while a pending
    flow (service creation, lock data, etc.) was active, the leaked
    pending state is cleaned up so the next text message won't be
    misrouted into the abandoned flow.
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(user_id)
    agg = self.aggregate_api_status()
    text = self.format_status_report(agg)

    await event.edit(
        text,
        buttons=[
            [Button.inline("🔄 Refresh", b"admin_refresh", style="primary"), Button.inline("⚙️ Model Settings", b"admin_models", style="success")],
            [Button.inline("🔌 Provider Settings", b"admin_providers", style="success")],
            [Button.inline("🔒 Manage Locks", b"admin_locks", style="primary")],
            [Button.inline("🏆 Token Leaders", b"admin_token_leaders", style="success")],
            [Button.inline("🚫 Block Management", b"admin_block_management", style="danger")],
            [Button.inline("🗑 Reset Usage", b"admin_reset", style="danger")],
            [Button.inline("📄 Export Report", b"admin_export", style="success")],
            [Button.inline("🤖 CW Classifier Model", b"cw_classifier_model", style="success")]
        ],
        parse_mode="html"
    )


async def admin_refresh(self: "TelegramBot", event: Any) -> None:
    """Refresh the admin dashboard stats.

    ``delete_pending_state`` cleans up any leaked pending state (see
    ``admin_panel`` docstring for rationale).
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return
    self.db.delete_pending_state(user_id)
    self._agg_cache = None
    agg = self.aggregate_api_status()
    await event.edit(
        self.format_status_report(agg),
        buttons=[
            [Button.inline("🔄 Refresh", b"admin_refresh", style="primary"), Button.inline("⚙️ Model Settings", b"admin_models", style="success")],
            [Button.inline("🔌 Provider Settings", b"admin_providers", style="success")],
            [Button.inline("🔒 Manage Locks", b"admin_locks", style="primary")],
            [Button.inline("🏆 Token Leaders", b"admin_token_leaders", style="success")],
            [Button.inline("🚫 Block Management", b"admin_block_management", style="danger")],
            [Button.inline("🗑 Reset Usage", b"admin_reset", style="danger")],
            [Button.inline("📄 Export Report", b"admin_export", style="success")]
        ],
        parse_mode="html"
    )


async def admin_reset(self: "TelegramBot", event: Any) -> None:
    """Reset ALL usage data: windows history+metrics, sessions, user_usage, group_usage.

    Performs a full reset of:
      - conversation_windows: history and per-window metrics
      - sessions (legacy): history and metrics
      - user_usage: persistent 12-hour per-user limits (requests, tokens, images, HTML)
      - group_usage: persistent per-group limits
      - in-memory AI_Service sessions

    Without resetting user_usage/group_usage, users would still hit their
    old limits even after the admin clicks "Reset Usage".
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return
    self.db.reset_all_sessions()
    self.db.reset_all_windows()
    # Reset persistent user/group usage tables (12-hour limits).
    # This is the ONLY call that clears user_usage and group_usage;
    # without it, users still hit old limits after admin reset.
    self.db.reset_usage_metrics()
    
    # Clear all in-memory sessions to force reload from database.
    # This ensures that the cleared history from the database is
    # properly reflected in memory when users send their next message.
    # Without this, users would continue to have old history in their
    # active sessions despite the database being cleared.
    self.sessions.clear()
    self._agg_cache = None
    agg = self.aggregate_api_status()
    await event.edit(
        self.format_status_report(agg),
        buttons=[
            [Button.inline("🔄 Refresh", b"admin_refresh", style="primary"), Button.inline("⚙️ Model Settings", b"admin_models", style="success")],
            [Button.inline("🔌 Provider Settings", b"admin_providers", style="success")],
            [Button.inline("🔒 Manage Locks", b"admin_locks", style="primary")],
            [Button.inline("🏆 Token Leaders", b"admin_token_leaders", style="success")],
            [Button.inline("🚫 Block Management", b"admin_block_management", style="danger")],
            [Button.inline("🗑 Reset Usage", b"admin_reset", style="danger")],
            [Button.inline("📄 Export Report", b"admin_export", style="success")]
        ],
        parse_mode="html"
    )


async def admin_export(self: "TelegramBot", event: Any) -> None:
    """Export a text report of system stats and per-user window metrics.

    ``delete_pending_state`` cleans up any leaked pending state.
    Reads from ``conversation_windows`` (the active data store) instead
    of the legacy ``sessions`` table which is never written to in normal
    operation and would return empty/stale data.
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return
    self.db.delete_pending_state(user_id)
    lines = [self.format_status_report(self.aggregate_api_status())]
    with self.db._get_conn() as conn:
        rows = conn.execute("""
            SELECT user_id,
                   SUM(total_requests) as total_requests,
                   SUM(total_input_tokens) as total_input_tokens,
                   SUM(total_output_tokens) as total_output_tokens
            FROM conversation_windows
            GROUP BY user_id
            ORDER BY (SUM(total_input_tokens) + SUM(total_output_tokens)) DESC
        """).fetchall()
    for row in rows:
        try:
            lines.append(f"--- user {row['user_id']} ---")
            lines.append(f"Requests: {row['total_requests']}, Input: {row['total_input_tokens']}, Output: {row['total_output_tokens']}")
        except Exception:
            lines.append(f"--- user {row['user_id']} --- error reading")
    await event.reply("\n".join(lines))


# ================================================================
# PROVIDER SETTINGS
# ================================================================

async def admin_providers(self: "TelegramBot", event: Any) -> None:
    """Show provider selection, health status, and configuration panel.

    delete_pending_state here serves as a safety net: if the admin
    navigated to this panel from anywhere (including via a cancel
    button or a command while a pending flow was active), any leaked
    pending state is cleaned up so the next text message won't be
    misrouted.
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(user_id)
    from api_http import get_active_provider, get_openai_config, GEMINI_API_KEYS, openai_pool, search_pool

    provider = get_active_provider(self.db)
    openai_config = get_openai_config(self.db)
    openai_keys_count = len(openai_config["api_keys"]) if openai_config["api_keys"] else 0

    gemini_mark = " ✅ فعال" if provider == "gemini" else ""
    openai_mark = " ✅ فعال" if provider == "openai" else ""

    gemini_health = "✅ سالم" if len(GEMINI_API_KEYS) > 0 else "❌ بدون کلید"
    openai_health = "✅ سالم" if openai_keys_count > 0 else "❌ بدون کلید"

    active_svc_id = self.db.get_setting("default_openai_service", "")
    active_svc_line = ""
    if provider == "openai" and active_svc_id:
        svc = self.db.get_service(active_svc_id)
        if svc:
            active_svc_line = f"  ┗ سرویس فعال: <code>{svc.get('name', active_svc_id)}</code>\n"

    text = (
        f"🔌 <b>تنظیمات Provider:</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Provider فعال:</b> <code>{provider}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🟢 <b>Gemini{gemini_mark}</b>\n"
        f"  ┗ کلیدها: <code>{len(GEMINI_API_KEYS)}</code>\n"
        f"  ┗ وضعیت: {gemini_health}\n\n"
        f"🔵 <b>OpenAI{openai_mark}</b>\n"
        f"  ┗ کلیدها: <code>{openai_keys_count}</code>\n"
        f"  ┗ وضعیت: {openai_health}\n"
        f"  ┗ Base URL: <code>{openai_config['base_url']}</code>\n"
        f"{active_svc_line}"
    )

    buttons = [
        [Button.inline("🟢 فعالسازی Gemini", b"set_provider:gemini", style="success" if provider == "gemini" else "primary"),
         Button.inline("🔵 فعالسازی OpenAI", b"set_provider:openai", style="success" if provider == "openai" else "primary")],
        [Button.inline("🤖 مدیریت سرویسها", b"admin_services", style="primary")],
        [Button.inline("🔌 تست اتصال", b"provider_test_connection", style="primary")],
        [Button.inline("📊 مصرف توکن", b"provider_token_dashboard", style="success")],
        [Button.inline("⚙️ تنظیمات OpenAI", b"admin_openai_config", style="primary")],
        [Button.inline("⚙️ تنظیمات مدلها", b"admin_models", style="success")],
        [Button.inline("🔙 بازگشت به پنل ادمین", b"admin_panel")]
    ]

    await event.edit(text, buttons=buttons, parse_mode="html")


async def provider_test_connection(self: "TelegramBot", event: Any) -> None:
    """Test connection to both providers."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    import asyncio
    from api_http import get_active_provider, get_openai_config, GEMINI_API_KEYS, search_pool

    await event.answer("🔄 در حال تست اتصال...", alert=True)

    results = []

    # Test Gemini
    if GEMINI_API_KEYS:
        try:
            client = search_pool.get_next_client()
            from api_http import AI_MODEL_SEARCH
            gemini_test_model = self.db.get_setting("search_model", AI_MODEL_SEARCH)
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=gemini_test_model,
                    contents=[{"role": "user", "parts": [{"text": "hi"}]}]
                ),
                timeout=15
            )
            if response and response.text:
                results.append("🟢 <b>Gemini:</b> ✅ سالم")
            else:
                results.append("🟢 <b>Gemini:</b> ⚠️ جواب خالی")
        except Exception as e:
            results.append(f"🟢 <b>Gemini:</b> ❌ خطا: {str(e)[:50]}")
    else:
        results.append("🟢 <b>Gemini:</b> ❌ بدون کلید")

    # Test OpenAI
    config = get_openai_config(self.db)
    keys = config["api_keys"] if config["api_keys"] else []
    if keys:
        try:
            # IMPORTANT: Create a throw-away pool for the test instead of
            # mutating the global openai_pool.  Mutating the global pool
            # would overwrite its internal keys/index state, potentially
            # causing a different key to be used for the next real request
            # than the round-robin intended.  The temporary pool is garbage-
            # collected after the test.
            from api_http import OpenAIClientPool
            pool = OpenAIClientPool(keys, config["base_url"])
            client = pool.get_next_client()
            openai_test_model = self.db.get_setting("openai_fallback_model", "mimo-v2.5-free")
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=openai_test_model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=10
                ),
                timeout=15
            )
            if response and response.choices:
                results.append("🔵 <b>OpenAI:</b> ✅ سالم")
            else:
                results.append("🔵 <b>OpenAI:</b> ⚠️ جواب خالی")
        except Exception as e:
            results.append(f"🔵 <b>OpenAI:</b> ❌ خطا: {str(e)[:50]}")
    else:
        results.append("🔵 <b>OpenAI:</b> ❌ بدون کلید")

    text = "🔌 <b>نتیجه تست اتصال:</b>\n\n" + "\n".join(results)
    await event.edit(
        text,
        buttons=[[Button.inline("🔄 تست مجدد", b"provider_test_connection"), Button.inline("🔙 بازگشت", b"admin_providers")]],
        parse_mode="html"
    )


async def provider_token_dashboard(self: "TelegramBot", event: Any) -> None:
    """Show token usage dashboard per provider."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    from api_http import get_active_provider
    provider = get_active_provider(self.db)

    agg = self.aggregate_api_status()

    # Use pre-aggregated values from aggregate_api_status instead of a
    # second full table scan (Bug #14).
    text = (
        f"📊 <b>داشبورد مصرف توکن:</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Provider فعال:</b> <code>{provider}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 <b>آمار کلی:</b>\n"
        f"  ┗ درخواستها: <code>{agg['total_requests']:,}</code>\n"
        f"  ┗ توکن ورودی: <code>{agg['total_input_tokens']:,}</code>\n"
        f"  ┗ توکن خروجی: <code>{agg['total_output_tokens']:,}</code>\n"
        f"  ┗ مجموع: <code>{agg['total_tokens']:,}</code>\n\n"
        f"👥 <b>کاربران فعال:</b> <code>{agg['instances']}</code>\n"
        f"💬 <b>پیامهای ذخیره:</b> <code>{agg['history_messages_count']}</code>\n"
    )

    await event.edit(
        text,
        buttons=[[Button.inline("🔄 بروزرسانی", b"provider_token_dashboard"), Button.inline("🔙 بازگشت", b"admin_providers")]],
        parse_mode="html"
    )


async def set_provider_cb(self: "TelegramBot", event: Any) -> None:
    """Switch the active AI provider with safe session handling.

    When the admin switches providers (Gemini ↔ OpenAI), we must also:
    1. Clear ALL per-window ``service_id`` assignments so stale window-level
       routing doesn't override the new global provider.
    2. Reload the ``ServiceManager`` to pick up any config changes.
    3. Invalidate in-memory ``AI_Service`` sessions so the next user request
       constructs a fresh instance with the correct provider.
    4. Refresh the OpenAI pool if switching to OpenAI (keys/URL may have changed).
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    data = event.data.decode().replace("set_provider:", "").strip()
    if data not in ("gemini", "openai"):
        await event.answer("Provider نامعتبر!", alert=True)
        return

    from api_http import get_active_provider, get_openai_config, OPENAI_API_KEYS, refresh_openai_pool, get_service_manager

    current_provider = get_active_provider(self.db)
    if current_provider == data:
        await event.answer(f"⚠️ در حال حاضر {data.upper()} فعال است.", alert=True)
        return

    if data == "openai":
        config = get_openai_config(self.db)
        keys = config["api_keys"] if config["api_keys"] else OPENAI_API_KEYS
        if not keys:
            await event.answer("❌ هیچ کلید API OpenAI تنظیم نشده! ابتدا کلید اضافه کنید.", alert=True)
            return

    active_count = len(self.processing_users)
    if active_count > 0:
        await event.answer(
            f"⚠️ {active_count} کاربر در حال پردازش هستند. سوئیچ انجام شد ولی سشنها حفظ شدند.",
            alert=True
        )

    # 1. Persist the new provider
    self.db.save_setting("active_provider", data)

    # 2. Clear ALL per-window service_id assignments to prevent stale routing.
    #    Without this, windows that were previously assigned to an OpenAI service
    #    would keep routing through OpenAI even after switching to Gemini (and
    #    vice versa for any future per-window Gemini assignments).
    cleared = self.db.clear_all_windows_service_id()
    tlogger.info(f"Provider switch to {data}: cleared service_id from {cleared} windows")

    # 3. Reload ServiceManager so it picks up latest service configs
    get_service_manager(self.db).reload()

    # 4. Refresh OpenAI pool if switching to OpenAI
    if data == "openai":
        refresh_openai_pool(self.db)

    # 5. Invalidate all in-memory sessions so next request uses the new provider
    self.invalidate_sessions_after_provider_change()
    self._agg_cache = None

    await event.answer(f"✅ Provider به {data.upper()} تغییر یافت.", alert=True)
    await admin_providers(self, event)


async def admin_openai_config(self: "TelegramBot", event: Any) -> None:
    """Show OpenAI configuration panel.

    Destination handler for cancel buttons in:
      - openai_set_base_url_start
      - openai_add_key_start
      - openai_remove_key_menu

    delete_pending_state cleans up any leaked pending state (e.g.
    OPENAI_SET_BASE_URL, OPENAI_ADD_KEY) when the admin navigates
    back here, preventing stale flows from capturing the next text.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(uid)
    from api_http import get_openai_config, OPENAI_MODEL_FALLBACK, OPENAI_DEFAULT_BASE_URL

    config = get_openai_config(self.db)
    keys = config["api_keys"] if config["api_keys"] else []

    base_url = self.db.get_setting("openai_base_url", OPENAI_DEFAULT_BASE_URL)
    search_model = self.db.get_setting("openai_search_model", OPENAI_MODEL_FALLBACK)
    quick_ask_model = self.db.get_setting("openai_quick_ask_model", OPENAI_MODEL_FALLBACK)
    mentors_model = self.db.get_setting("openai_mentors_model", OPENAI_MODEL_FALLBACK)
    fallback_model = self.db.get_setting("openai_fallback_model", OPENAI_MODEL_FALLBACK)

    keys_display = ""
    for i, k in enumerate(keys):
        masked = k[:8] + "..." + k[-4:] if len(k) > 12 else k[:4] + "..."
        keys_display += f"  {i+1}. <code>{masked}</code>\n"

    if not keys_display:
        keys_display = "  ❌ هیچ کلیدی تنظیم نشده\n"

    text = (
        f"🔵 <b>تنظیمات OpenAI:</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Base URL:</b>\n<code>{base_url}</code>\n\n"
        f"<b>کلیدهای API ({len(keys)}):</b>\n{keys_display}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>مدلها:</b>\n"
        f"  سرچ وب: <code>{search_model}</code>\n"
        f"  سوال سریع: <code>{quick_ask_model}</code>\n"
        f"  منتورها: <code>{mentors_model}</code>\n"
        f"  پشتیبان: <code>{fallback_model}</code>\n"
    )

    await event.edit(
        text,
        buttons=[
            [Button.inline("🔗 تغییر Base URL", b"openai_set_base_url")],
            [Button.inline("➕ افزودن کلید", b"openai_add_key"), Button.inline("🗑 حذف کلید", b"openai_remove_key")],
            [Button.inline("🤖 تغییر مدل سرچ", b"change_model:openai_search_model")],
            [Button.inline("🤖 تغییر مدل سوال سریع", b"change_model:openai_quick_ask_model")],
            [Button.inline("🤖 تغییر مدل منتورها", b"change_model:openai_mentors_model")],
            [Button.inline("🤖 تغییر مدل پشتیبان", b"change_model:openai_fallback_model")],
            [Button.inline("🔙 بازگشت", b"admin_providers")]
        ],
        parse_mode="html"
    )


# ================================================================
# MULTI-SERVICE MANAGEMENT
# ================================================================

async def admin_services(self: "TelegramBot", event: Any) -> None:
    """Show all configured AI services.

    This is a destination handler for several "cancel" buttons across the
    service create/edit flows.  We call delete_pending_state at the top so
    that any leaked pending state (e.g. SERVICE_CREATE_ID, SERVICE_SET_URL)
    is cleaned up when the admin navigates back here.  Without this, the
    next text message the admin sends would be routed into whichever flow
    was abandoned, causing confusing behavior.

    Also cleans up temporary ``_temp_service_*`` settings left behind if
    the admin abandoned a multi-step service creation flow midway.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(uid)
    # Clean up temporary service creation keys that may have been left
    # behind if the admin cancelled mid-flow.
    for _temp_key in ("_temp_service_id", "_temp_service_name", "_temp_service_url", "_temp_service_keys"):
        self.db.save_setting(_temp_key, "")
    services = self.db.get_services()
    active_service_id = self.db.get_setting("default_openai_service", "")

    text = "🤖 <b>مدیریت سرویسهای AI:</b>\n\n"
    buttons = []

    if not services:
        text += "هیچ سرویسی تنظیم نشده.\n"
        text += "از دکمه زیر یک سرویس جدید اضافه کنید.\n"
    else:
        for svc in services:
            svc_id = svc.get("id", "?")
            svc_name = svc.get("name", svc_id)
            keys_count = len(svc.get("api_keys", []))
            base_url = svc.get("base_url", "N/A")
            is_active = svc_id == active_service_id
            status_mark = " ✅ فعال" if is_active else ""

            text += f"🔵 <b>{svc_name}{status_mark}</b>\n"
            text += f"  ┗ ID: <code>{svc_id}</code>\n"
            text += f"  ┗ کلیدها: <code>{keys_count}</code>\n"
            text += f"  ┗ URL: <code>{base_url[:40]}...</code>\n\n"

            row = [Button.inline(f"✏️ {svc_name}", f"service_edit:{svc_id}".encode())]
            if is_active:
                row.append(Button.inline("❌ غیرفعال", b"deactivate_service", style="danger"))
            else:
                row.append(Button.inline("🔌 فعال", f"activate_service:{svc_id}".encode(), style="success"))
            row.append(Button.inline(f"🗑", f"service_delete_confirm:{svc_id}".encode(), style="danger"))
            buttons.append(row)

    buttons.append([Button.inline("➕ افزودن سرویس جدید", b"service_create_start", style="success")])
    buttons.append([Button.inline("🔙 بازگشت", b"admin_providers")])

    await event.edit(text, buttons=buttons, parse_mode="html")


async def service_create_start(self: "TelegramBot", event: Any) -> None:
    """Start creating a new service."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_pending_state(uid, pending_question=False, pending_action="SERVICE_CREATE_ID")
    msg = await event.edit(
        "➕ <b>افزودن سرویس جدید:</b>\n\n"
        "یک شناسه یکتا برای سرویس وارد کنید (فقط حروف انگلیسی و عدد):\n\n"
        "مثال:\n"
        "<code>nvidia</code>\n"
        "<code>agentrouter</code>\n"
        "<code>groq</code>",
        buttons=[[Button.inline("🔙 انصراف", b"admin_services")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "service_create")] = msg


async def service_edit(self: "TelegramBot", event: Any) -> None:
    """Show edit panel for a specific service.

    Serves as the "cancel" destination for three child flows:
      - service_set_url_start
      - service_add_key_start
      - service_set_model_start

    Each of those flows sets a pending_action like ``SERVICE_SET_URL:{svc_id}``
    and provides a cancel button that calls back to this handler.
    Without delete_pending_state here, those pending states would leak:
    the cancelled flow would still be active in the DB, and the next text
    the admin sends would be misinterpreted by pending_message_handler.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(uid)

    svc_id = event.data.decode().replace("service_edit:", "").strip()
    svc = self.db.get_service(svc_id)
    if not svc:
        await event.answer("سرویس یافت نشد!", alert=True)
        return

    svc_name = svc.get("name", svc_id)
    base_url = svc.get("base_url", "N/A")
    keys = svc.get("api_keys", [])
    models = svc.get("models", {})

    keys_display = ""
    for i, k in enumerate(keys):
        masked = k[:8] + "..." + k[-4:] if len(k) > 12 else k[:4] + "..."
        keys_display += f"  {i+1}. <code>{masked}</code>\n"
    if not keys_display:
        keys_display = "  ❌ بدون کلید\n"

    text = (
        f"✏️ <b>ویرایش سرویس: {svc_name}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>ID:</b> <code>{svc_id}</code>\n"
        f"<b>Base URL:</b>\n<code>{base_url}</code>\n\n"
        f"<b>کلیدها ({len(keys)}):</b>\n{keys_display}\n"
        f"<b>مدلها:</b>\n"
        f"  سرچ: <code>{models.get('search', 'N/A')}</code>\n"
        f"  سوال سریع: <code>{models.get('quick_ask', 'N/A')}</code>\n"
        f"  منتورها: <code>{models.get('mentors', 'N/A')}</code>\n"
        f"  پشتیبان: <code>{models.get('fallback', 'N/A')}</code>\n"
    )

    buttons = [
        [Button.inline("🔗 تغییر Base URL", f"service_set_url:{svc_id}".encode())],
        [Button.inline("➕ افزودن کلید", f"service_add_key:{svc_id}".encode()),
         Button.inline("🗑 حذف کلید", f"service_remove_key:{svc_id}".encode())],
        [Button.inline("🤖 تغییر مدل سرچ", f"service_set_model:{svc_id}:search".encode())],
        [Button.inline("🤖 تغییر مدل سوال سریع", f"service_set_model:{svc_id}:quick_ask".encode())],
        [Button.inline("🤖 تغییر مدل منتورها", f"service_set_model:{svc_id}:mentors".encode())],
        [Button.inline("🤖 تغییر مدل پشتیبان", f"service_set_model:{svc_id}:fallback".encode())],
        [Button.inline("🔌 تست اتصال", f"service_test:{svc_id}".encode())],
        [Button.inline("🔙 بازگشت", b"admin_services")]
    ]

    await event.edit(text, buttons=buttons, parse_mode="html")


async def service_delete_confirm(self: "TelegramBot", event: Any) -> None:
    """Show a confirmation dialog before deleting a service.

    The "✅ بله، حذف کن" button emits data ``service_delete:{svc_id}``.
    Because Telethon uses re.match() and ``service_delete:`` is a literal
    prefix of ``service_delete_confirm:``, a bare pattern like
    ``pattern=r"service_delete:"`` would ALSO match this handler's own
    callback data.  The fix (in bot.py) uses a negative lookahead:
    ``pattern=r"^service_delete:(?!confirm)"`` so that only the confirm
    handler matches the confirm pattern, and only the delete handler
    matches the delete pattern.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    svc_id = event.data.decode().replace("service_delete_confirm:", "").strip()
    svc = self.db.get_service(svc_id)
    if not svc:
        await event.answer("سرویس یافت نشد!", alert=True)
        return

    svc_name = svc.get("name", svc_id)
    await event.edit(
        f"🗑 <b>تأیید حذف سرویس:</b>\n\n"
        f"آیا مطمئن هستید که میخواهید سرویس <b>{svc_name}</b> را حذف کنید؟\n\n"
        f"⚠️ پنجرههای متصل به این سرویس به Gemini برمیگردند.",
        buttons=[
            [Button.inline("✅ بله، حذف کن", f"service_delete:{svc_id}".encode(), style="danger")],
            [Button.inline("❌ انصراف", b"admin_services")]
        ],
        parse_mode="html"
    )


async def service_delete(self: "TelegramBot", event: Any) -> None:
    """Delete a service (actual deletion, not confirmation).

    Only reachable from the "✅ بله، حذف کن" button inside
    service_delete_confirm.  The regex in bot.py ensures this handler
    does NOT fire for ``service_delete_confirm:...`` data — see
    ``_register_handlers`` for the negative lookahead explanation.

    After deletion, clears any per-window service_id references to the
    deleted service so they don't hold stale IDs.  The ``AI_Service``
    constructor already handles missing services gracefully (falls back
    to Gemini), but cleaning up at the DB level prevents repeated
    lookups against a non-existent service.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    svc_id = event.data.decode().replace("service_delete:", "").strip()
    if self.db.delete_service(svc_id):
        # Clean up per-window references to the deleted service.
        # AI_Service.__init__ would handle this at runtime (has_service
        # returns False → falls back to Gemini), but proactive cleanup
        # avoids repeated stale lookups.
        self.db.clear_all_windows_service_id()

        from api_http import get_service_manager
        get_service_manager(self.db).reload()
        self.invalidate_sessions_after_provider_change()
        await event.answer("✅ سرویس حذف شد.", alert=True)
    else:
        await event.answer("❌ خطا در حذف سرویس!", alert=True)
    await admin_services(self, event)


async def activate_service(self: "TelegramBot", event: Any) -> None:
    """Set a service as the default for OpenAI provider.

    Activates the service globally and assigns it to all conversation windows
    that currently have no service_id, so existing users immediately route
    through the new service without waiting for their next message.
    Also invalidates in-memory sessions to force clean re-initialization.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    svc_id = event.data.decode().replace("activate_service:", "").strip()
    svc = self.db.get_service(svc_id)
    if not svc:
        await event.answer("❌ سرویس یافت نشد!", alert=True)
        return

    if not svc.get("api_keys"):
        await event.answer("❌ این سرویس کلید API ندارد!", alert=True)
        return

    self.db.save_setting("default_openai_service", svc_id)
    self.db.save_setting("active_provider", "openai")

    # Assign this service to all windows that don't already have one,
    # so they immediately route through the new service.
    assigned = self.db.assign_service_to_unassigned_windows(svc_id)
    tlogger.info(f"Activate service '{svc_id}': assigned to {assigned} unassigned windows")

    from api_http import refresh_openai_pool, get_service_manager
    get_service_manager(self.db).reload()
    refresh_openai_pool(self.db)
    self.invalidate_sessions_after_provider_change()
    self._agg_cache = None

    await event.answer(f"✅ سرویس «{svc.get('name', svc_id)}» فعال شد.", alert=True)
    await admin_services(self, event)


async def deactivate_service(self: "TelegramBot", event: Any) -> None:
    """Deactivate OpenAI service and switch back to Gemini.

    Clears all per-window service_id assignments so windows immediately
    route through Gemini instead of continuing to use the stale OpenAI
    service reference.  Also invalidates in-memory sessions.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_setting("default_openai_service", "")
    self.db.save_setting("active_provider", "gemini")

    # Clear per-window service assignments to prevent stale OpenAI routing
    cleared = self.db.clear_all_windows_service_id()
    tlogger.info(f"Deactivate service: cleared service_id from {cleared} windows, switched to Gemini")

    # Reload ServiceManager and invalidate cached sessions
    from api_http import get_service_manager
    get_service_manager(self.db).reload()
    self.invalidate_sessions_after_provider_change()
    self._agg_cache = None

    await event.answer("✅ Gemini فعال شد.", alert=True)
    await admin_services(self, event)


async def service_test(self: "TelegramBot", event: Any) -> None:
    """Test connection to a specific service."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    import asyncio
    svc_id = event.data.decode().replace("service_test:", "").strip()
    svc = self.db.get_service(svc_id)
    if not svc:
        await event.answer("سرویس یافت نشد!", alert=True)
        return

    await event.answer("🔄 در حال تست اتصال...", alert=True)

    from api_http import OpenAIClientPool
    keys = svc.get("api_keys", [])
    base_url = svc.get("base_url", "")
    models = svc.get("models", {})

    # Prefer the quick_ask model (the one users primarily interact with)
    # for a realistic health check; fall back to the catch-all model.
    # A hardcoded default (gpt-3.5-turbo) is only used if neither is
    # configured — which should not happen in normal operation.
    test_model = models.get("quick_ask") or models.get("fallback", "gpt-3.5-turbo")

    if not keys:
        await event.edit(
            f"❌ <b>تست اتصال: {svc.get('name', svc_id)}</b>\n\nهیچ کلید API تنظیم نشده.",
            buttons=[[Button.inline("🔙 بازگشت", f"service_edit:{svc_id}".encode())]],
            parse_mode="html"
        )
        return

    pool = OpenAIClientPool(keys, base_url)
    try:
        client = pool.get_next_client()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10
            ),
            timeout=20
        )
        if response and response.choices:
            await event.edit(
                f"✅ <b>تست اتصال: {svc.get('name', svc_id)}</b>\n\nاتصال سالم است.\nمدل تست: <code>{test_model}</code>",
                buttons=[[Button.inline("🔙 بازگشت", f"service_edit:{svc_id}".encode())]],
                parse_mode="html"
            )
        else:
            await event.edit(
                f"⚠️ <b>تست اتصال: {svc.get('name', svc_id)}</b>\n\nجواب خالی دریافت شد.",
                buttons=[[Button.inline("🔙 بازگشت", f"service_edit:{svc_id}".encode())]],
                parse_mode="html"
            )
    except Exception as e:
        await event.edit(
            f"❌ <b>تست اتصال: {svc.get('name', svc_id)}</b>\n\nخطا: <code>{str(e)[:100]}</code>",
            buttons=[[Button.inline("🔙 بازگشت", f"service_edit:{svc_id}".encode())]],
            parse_mode="html"
        )


async def service_set_url_start(self: "TelegramBot", event: Any) -> None:
    """Start setting service base URL."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    svc_id = event.data.decode().replace("service_set_url:", "").strip()
    svc = self.db.get_service(svc_id)
    current = svc.get("base_url", "N/A") if svc else "N/A"

    self.db.save_pending_state(uid, pending_question=False, pending_action=f"SERVICE_SET_URL:{svc_id}")
    msg = await event.edit(
        f"🔗 <b>تغییر Base URL سرویس:</b>\n\n"
        f"URL فعلی: <code>{current}</code>\n\n"
        f"URL جدید را وارد کنید:",
        buttons=[[Button.inline("🔙 انصراف", f"service_edit:{svc_id}".encode())]],
        parse_mode="html"
    )
    self.pending_message[(uid, "service_set_url")] = msg


async def service_add_key_start(self: "TelegramBot", event: Any) -> None:
    """Start adding API key to service."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    svc_id = event.data.decode().replace("service_add_key:", "").strip()

    self.db.save_pending_state(uid, pending_question=False, pending_action=f"SERVICE_ADD_KEY:{svc_id}")
    msg = await event.edit(
        "➕ <b>افزودن کلید API:</b>\n\n"
        "کلید(ها) را وارد کنید (هر کلید در یک خط):",
        buttons=[[Button.inline("🔙 انصراف", f"service_edit:{svc_id}".encode())]],
        parse_mode="html"
    )
    self.pending_message[(uid, "service_add_key")] = msg


async def service_set_model_start(self: "TelegramBot", event: Any) -> None:
    """Start setting service model."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    data = event.data.decode().replace("service_set_model:", "").strip()
    parts = data.split(":", 1)
    svc_id = parts[0] if len(parts) > 0 else ""
    model_type = parts[1] if len(parts) > 1 else "fallback"

    svc = self.db.get_service(svc_id)
    current = svc.get("models", {}).get(model_type, "N/A") if svc else "N/A"

    self.db.save_pending_state(uid, pending_question=False, pending_action=f"SERVICE_SET_MODEL:{svc_id}:{model_type}")
    msg = await event.edit(
        f"🤖 <b>تغییر مدل ({model_type}):</b>\n\n"
        f"مدل فعلی: <code>{current}</code>\n\n"
        f"نام مدل جدید را وارد کنید:",
        buttons=[[Button.inline("🔙 انصراف", f"service_edit:{svc_id}".encode())]],
        parse_mode="html"
    )
    self.pending_message[(uid, "service_set_model")] = msg


async def service_remove_key_menu(self: "TelegramBot", event: Any, svc_id: Optional[str] = None) -> None:
    """Show service key removal menu.

    Args:
        event: The Telethon callback event.
        svc_id: Optional service ID override.  When ``None``, the service
            ID is extracted from ``event.data``.  This parameter exists so
            that ``service_remove_key_confirm`` can call this function
            directly without relying on the (stale) event.data format.

    Why the ``svc_id`` override?
        ``service_remove_key_confirm`` emits callback data like
        ``service_remove_key_confirm:{svc_id}:{index}``.  After the key
        is removed it needs to refresh this menu, but the event.data
        still carries the ``_confirm`` prefix.  Passing ``svc_id``
        explicitly avoids the string-mismatch bug where
        ``replace("service_remove_key:", "")`` would fail to strip the
        ``service_remove_key_confirm:`` prefix (because the colon
        position differs).
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    if svc_id is None:
        svc_id = event.data.decode().replace("service_remove_key:", "").strip()
    svc = self.db.get_service(svc_id)
    if not svc:
        await event.answer("سرویس یافت نشد!", alert=True)
        return

    keys = svc.get("api_keys", [])
    if not keys:
        await event.answer("هیچ کلیدی برای حذف وجود ندارد.", alert=True)
        return

    text = "🗑 <b>حذف کلید:</b>\n\n"
    buttons = []
    for i, k in enumerate(keys):
        masked = k[:8] + "..." + k[-4:] if len(k) > 12 else k[:4] + "..."
        text += f"{i+1}. <code>{masked}</code>\n"
        buttons.append([Button.inline(f"🗑 حذف {i+1}: {masked}", f"service_remove_key_confirm:{svc_id}:{i}".encode())])

    buttons.append([Button.inline("🔙 بازگشت", f"service_edit:{svc_id}".encode())])
    await event.edit(text, buttons=buttons, parse_mode="html")


async def service_remove_key_confirm(self: "TelegramBot", event: Any) -> None:
    """Remove a specific key from a service and refresh the key list.

    After removal, explicitly passes ``svc_id`` to
    ``service_remove_key_menu`` because the event.data still carries
    the ``service_remove_key_confirm:{svc_id}:{index}`` format which
    the menu handler cannot parse (the colon position differs from
    ``service_remove_key:``).
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    data = event.data.decode().replace("service_remove_key_confirm:", "").strip()
    parts = data.split(":", 1)
    svc_id = parts[0]
    index = int(parts[1]) if len(parts) > 1 else -1

    svc = self.db.get_service(svc_id)
    if not svc or index < 0 or index >= len(svc.get("api_keys", [])):
        await event.answer("کلید یافت نشد!", alert=True)
        return

    removed = svc["api_keys"].pop(index)
    self.db.save_service(svc)
    from api_http import get_service_manager
    get_service_manager(self.db).reload()

    masked = removed[:8] + "..." + removed[-4:] if len(removed) > 12 else removed
    await event.answer(f"کلید {masked} حذف شد.", alert=True)
    # Pass svc_id explicitly because event.data still carries the
    # "service_remove_key_confirm:" prefix which would cause
    # service_remove_key_menu to extract a wrong svc_id.
    await service_remove_key_menu(self, event, svc_id=svc_id)


async def openai_set_base_url_start(self: "TelegramBot", event: Any) -> None:
    """Start setting OpenAI base URL."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    from api_http import OPENAI_DEFAULT_BASE_URL
    current = self.db.get_setting("openai_base_url", OPENAI_DEFAULT_BASE_URL)

    self.db.save_pending_state(uid, pending_question=False, pending_action="OPENAI_SET_BASE_URL")
    msg = await event.edit(
        f"🔗 <b>تغییر Base URL OpenAI:</b>\n\n"
        f"URL فعلی: <code>{current}</code>\n\n"
        f"لطفا URL جدید را ارسال کنید:\n"
        f"<code>https://api.openai.com/v1</code>\n"
        f"<code>https://router.openai.com/v1</code>\n\n"
        f"یا انصراف دهید:",
        buttons=[[Button.inline("🔙 انصراف", b"admin_openai_config")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "openai_base_url")] = msg


async def openai_add_key_start(self: "TelegramBot", event: Any) -> None:
    """Start adding OpenAI API key."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_pending_state(uid, pending_question=False, pending_action="OPENAI_ADD_KEY")
    msg = await event.edit(
        "➕ <b>افزودن کلید API OpenAI:</b>\n\n"
        "لطفا کلید API را ارسال کنید:\n\n"
        "مثال:\n"
        "<code>sk-abcdefghijklmnop1234567890</code>\n\n"
        "⚠️ میتوانید چندین کلید را در خطوط جداگانه ارسال کنید.",
        buttons=[[Button.inline("🔙 انصراف", b"admin_openai_config")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "openai_add_key")] = msg


async def openai_remove_key_menu(self: "TelegramBot", event: Any) -> None:
    """Show OpenAI key removal menu."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    from api_http import get_openai_config
    config = get_openai_config(self.db)
    keys = config["api_keys"] if config["api_keys"] else []

    if not keys:
        await event.answer("هیچ کلیدی برای حذف وجود ندارد.", alert=True)
        return

    text = "🗑 <b>حذف کلید OpenAI:</b>\n\nروی کلید مورد نظر کلیک کنید:\n\n"
    buttons = []
    for i, k in enumerate(keys):
        masked = k[:8] + "..." + k[-4:] if len(k) > 12 else k[:4] + "..."
        text += f"{i+1}. <code>{masked}</code>\n"
        buttons.append([Button.inline(f"🗑 حذف کلید {i+1}: {masked}", f"openai_remove_key_confirm:{i}".encode())])

    buttons.append([Button.inline("🗑 حذف همه", b"openai_remove_all_keys_confirm", style="danger")])
    buttons.append([Button.inline("🔙 بازگشت", b"admin_openai_config")])

    await event.edit(text, buttons=buttons, parse_mode="html")


async def openai_remove_key_confirm(self: "TelegramBot", event: Any) -> None:
    """Remove a specific OpenAI API key."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    data = event.data.decode().replace("openai_remove_key_confirm:", "").strip()
    try:
        index = int(data)
    except ValueError:
        await event.answer("خطا!", alert=True)
        return

    from api_http import get_openai_config, refresh_openai_pool
    config = get_openai_config(self.db)
    keys = config["api_keys"] if config["api_keys"] else []

    if index < 0 or index >= len(keys):
        await event.answer("کلید یافت نشد!", alert=True)
        return

    removed = keys.pop(index)
    self.db.save_setting("openai_api_keys", json.dumps(keys))
    refresh_openai_pool(self.db)

    masked = removed[:8] + "..." + removed[-4:] if len(removed) > 12 else removed
    await event.answer(f"کلید {masked} حذف شد.", alert=True)
    await openai_remove_key_menu(self, event)


async def openai_remove_all_keys_confirm(self: "TelegramBot", event: Any) -> None:
    """Confirm before removing all OpenAI API keys."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    from api_http import get_active_provider
    provider = get_active_provider(self.db)
    warning = ""
    if provider == "openai":
        warning = "\n\n⚠️ <b>هشدار:</b> Provider فعال OpenAI است! با حذف کلیدها، ربات قادر به پاسخگویی نخواهد بود."

    await event.edit(
        f"🗑 <b>تأیید حذف همه کلیدها:</b>\n\n"
        f"آیا مطمئن هستید که میخواهید تمام کلیدهای OpenAI را حذف کنید؟"
        f"{warning}",
        buttons=[
            [Button.inline("✅ بله، حذف کن", b"openai_remove_all_keys", style="danger")],
            [Button.inline("❌ انصراف", b"openai_remove_key")]
        ],
        parse_mode="html"
    )


async def openai_remove_all_keys(self: "TelegramBot", event: Any) -> None:
    """Remove all OpenAI API keys."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_setting("openai_api_keys", "[]")
    from api_http import refresh_openai_pool
    refresh_openai_pool(self.db)

    await event.answer("تمام کلیدهای OpenAI حذف شدند.", alert=True)
    await admin_openai_config(self, event)


# ================================================================
# MODEL SETTINGS
# ================================================================

async def admin_models(self: "TelegramBot", event: Any) -> None:
    """Show model configuration panel.

    Destination handler for cancel buttons in:
      - openai_custom_model_start
      - change_model_menu

    delete_pending_state prevents leaked states like
    OPENAI_CUSTOM_MODEL:* from persisting after the admin
    navigates away.
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(user_id)
    from api_http import get_active_provider
    provider = get_active_provider(self.db)
    agg = self.aggregate_api_status()

    if provider == "openai":
        text = (
            f"⚙️ <b>تنظیمات مدلها (OpenAI فعال):</b>\n\n"
            f"۱. مدل سرچ وب: <code>{agg.get('search_model', 'N/A')}</code>\n"
            f"۲. مدل سوال سریع: <code>{self.db.get_setting('openai_quick_ask_model', 'N/A')}</code>\n"
            f"۳. مدل منتورها: <code>{self.db.get_setting('openai_mentors_model', 'N/A')}</code>\n"
            f"۴. مدل پشتیبان: <code>{self.db.get_setting('openai_fallback_model', 'N/A')}</code>\n"
        )
        buttons = [
            [Button.inline("تغییر مدل سوال سریع", b"change_model:openai_quick_ask_model")],
            [Button.inline("تغییر مدل منتورها", b"change_model:openai_mentors_model")],
            [Button.inline("تغییر مدل پشتیبان", b"change_model:openai_fallback_model")],
            [Button.inline("🔙 بازگشت به پنل ادمین", b"admin_panel")]
        ]
    else:
        text = (
            f"⚙️ <b>تنظیمات مدلها (Gemini فعال):</b>\n\n"
            f"۱. مدل سرچ وب: <code>{agg['search_model']}</code>\n"
            f"۲. مدل سوال سریع: <code>{agg['quick_ask_model']}</code>\n"
            f"۳. مدل منتورها: <code>{agg['mentors_model']}</code>\n"
            f"۴. مدل پشتیبان: <code>{agg['fallback_model']}</code>\n"
        )
        buttons = [
            [Button.inline("تغییر مدل سرچ وب", b"change_model:search_model")],
            [Button.inline("تغییر مدل سوال سریع", b"change_model:quick_ask_model")],
            [Button.inline("تغییر مدل منتورها", b"change_model:mentors_model")],
            [Button.inline("تغییر مدل پشتیبان", b"change_model:fallback_model")],
            [Button.inline("🔙 بازگشت به پنل ادمین", b"admin_panel")]
        ]

    await event.edit(text, buttons=buttons, parse_mode="html")


async def change_model_menu(self: "TelegramBot", event: Any) -> None:
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    key = event.data.decode().replace("change_model:", "").strip()

    if key.startswith("openai_"):
        current_val = self.db.get_setting(key, "mimo-v2.5-free")

        def mark(model_name):
            return " ✅" if current_val == model_name else ""

        await event.edit(
            f"لطفا مدل جدید را برای <code>{key}</code> انتخاب کنید:\n\n"
            f"مدل فعلی: <code>{current_val}</code>\n\n"
            f"یا نام مدل دلخواه را تایپ کنید:",
            buttons=[
                [Button.inline(f"mimo-v2.5-free{mark('mimo-v2.5-free')}", f"set_model:{key}:mimo-v2.5-free".encode())],
                [Button.inline(f"mimo-v2.5-pro-free{mark('mimo-v2.5-pro-free')}", f"set_model:{key}:mimo-v2.5-pro-free".encode())],
                [Button.inline(f"mistral-large{mark('mistral-large')}", f"set_model:{key}:mistral-large".encode())],
                [Button.inline(f"mistral-medium-3-5{mark('mistral-medium-3-5')}", f"set_model:{key}:mistral-medium-3-5".encode())],
                [Button.inline("✏️ تایپ مدل دلخواه", f"openai_custom_model_start:{key}".encode())],
                [Button.inline("🔙 لغو و بازگشت", b"admin_models")]
            ],
            parse_mode="html"
        )
    else:
        current_val = self.db.get_setting(key, "gemini-3.5-flash")

        def mark(model_name):
            return " ✅" if current_val == model_name else ""

        await event.edit(
            f"لطفا مدل جدید را برای <code>{key}</code> انتخاب کنید:\n\n"
            f"مدل فعلی: <code>{current_val}</code>",
            buttons=[
                [Button.inline(f"gemini-3.5-flash{mark('gemini-3.5-flash')}", f"set_model:{key}:gemini-3.5-flash".encode())],
                [Button.inline(f"gemini-3.1-flash-lite{mark('gemini-3.1-flash-lite')}", f"set_model:{key}:gemini-3.1-flash-lite".encode())],
                [Button.inline(f"gemini-2.5-flash{mark('gemini-2.5-flash')}", f"set_model:{key}:gemini-2.5-flash".encode())],
                [Button.inline("🔙 لغو و بازگشت", b"admin_models")]
            ],
            parse_mode="html"
        )


async def openai_custom_model_start(self: "TelegramBot", event: Any) -> None:
    """Start entering custom OpenAI model name."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    data = event.data.decode().replace("openai_custom_model_start:", "").strip()
    self.db.save_pending_state(uid, pending_question=False, pending_action=f"OPENAI_CUSTOM_MODEL:{data}")
    msg = await event.edit(
        f"✏️ <b>تایپ مدل دلخواه برای <code>{data}</code>:</b>\n\n"
        "نام مدل را ارسال کنید:",
        buttons=[[Button.inline("🔙 انصراف", b"admin_models")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "openai_custom_model")] = msg


async def set_model_value(self: "TelegramBot", event: Any) -> None:
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    data = event.data.decode().replace("set_model:", "").strip()
    parts = data.split(":", 1)
    if len(parts) < 2:
        return

    key = parts[0]
    model_value = parts[1]

    self.db.save_setting(key, model_value)
    await event.answer(f"مدل {key} با موفقیت به {model_value} تغییر یافت.", alert=True)
    await admin_models(self, event)


# ================================================================
# LOCK MANAGEMENT
# ================================================================

async def admin_locks(self: "TelegramBot", event: Any) -> None:
    """Show lock management panel.

    Destination handler for cancel buttons in add_lock_start.
    delete_pending_state cleans up any leaked ADD_LOCK_DATA pending
    state when the admin navigates back here.
    """
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(user_id)
    locks = self.db.get_locks()
    text = "🔒 <b>مدیریت قفلهای جوین اجباری:</b>\n\n"
    buttons = []

    if not locks:
        text += "هیچ قفلی در حال حاضر فعال نیست."
    else:
        for lock in locks:
            stats = self.db.get_join_stats(lock["channel_id"])
            text += (
                f"📌 <b>{lock['title']}</b>\n"
                f"🔹 آیدی عددی: <code>{lock['channel_id']}</code>\n"
                f"🔗 لینک: {lock['invite_link']}\n"
                f"👥 کل جوینها: <code>{stats['total']}</code>\n"
                f"📅 جوینهای ۲۴ ساعت اخیر: <code>{stats['last_24h']}</code>\n\n"
            )
            buttons.append([Button.inline(f"🗑 حذف {lock['title']}", f"delete_lock:{lock['channel_id']}".encode())])

    buttons.append([Button.inline("➕ افزودن قفل جدید", b"add_lock_start")])
    buttons.append([Button.inline("🔙 بازگشت به پنل ادمین", b"admin_panel")])

    await event.edit(text, buttons=buttons, parse_mode="html")


async def add_lock_start(self: "TelegramBot", event: Any) -> None:
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_pending_state(user_id, pending_question=False, pending_action="ADD_LOCK_DATA")
    msg = await event.edit(
        "✍️ لطفا اطلاعات قفل جدید را ارسال کنید.\n\n"
        "میتوانید اطلاعات را با علامت <code>|</code>، کاما، فاصله یا در خطوط جداگانه بفرستید:\n"
        "<code>آیدی عددی کانال/گروه | نام کانال/گروه | لینک دعوت</code>\n\n"
        "مثال:\n"
        "<code>-100123456789 | کانال تست | https://t.me/+abcde</code>\n"
        "یا:\n"
        "<code>-100123456789 کانال_تست https://t.me/+abcde</code>",
        buttons=[[Button.inline("🔙 انصراف", b"admin_locks")]],
        parse_mode="html"
    )
    self.pending_message[(user_id, "add_lock")] = msg


async def delete_lock(self: "TelegramBot", event: Any) -> None:
    user_id = event.sender_id
    if user_id not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    channel_id = int(event.data.decode().replace("delete_lock:", "").strip())
    self.db.remove_lock(channel_id)
    await event.answer("قفل با موفقیت حذف شد.", alert=True)
    await admin_locks(self, event)


# ================================================================
# SUBSCRIPTION & USER MANAGEMENT
# ================================================================

async def admin_toggle_sub_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    target_uid = int(event.data.decode().split(":")[1])
    current_sub = self.get_user_subscription(target_uid)
    new_sub = "free" if current_sub == "paid" else "paid"

    self.set_user_subscription(target_uid, new_sub)
    await event.answer(f"اشتراک کاربر به {new_sub.upper()} تغییر یافت.", alert=True)
    from .commands import send_user_management_panel
    await send_user_management_panel(self, event, target_uid)


async def admin_reset_user_limit_cb(self: "TelegramBot", event: Any) -> None:
    """Reset ALL 12-hour usage limits for a specific user.

    Deletes the entire user_usage row (requests, tokens, images, HTML files).
    The row is automatically recreated on the user's next request via the
    INSERT ON CONFLICT pattern in increment_user_usage / increment_image_usage.
    Previously this only cleared requests and tokens, leaving image and HTML
    limits intact — which confused admins who expected a full reset.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    target_uid = int(event.data.decode().split(":")[1])

    try:
        with self.db._get_conn() as conn:
            conn.execute("DELETE FROM user_usage WHERE user_id = ?", (target_uid,))
            conn.commit()
    except Exception as e:
        tlogger.error(f"Failed to reset user usage in DB: {e}")
        await event.answer("خطا در ریست دیتابیس!", alert=True)
        return

    if target_uid in self.sessions:
        self.sessions[target_uid].reset_usage_metrics_only()

    await event.answer("محدودیت مصرف ۱۲ ساعته کاربر با موفقیت ریست شد.", alert=True)
    from .commands import send_user_management_panel
    await send_user_management_panel(self, event, target_uid)


async def admin_clear_user_windows_cb(self: "TelegramBot", event: Any) -> None:
    """Clear all conversation windows AND reset 12-hour usage limits for a user.

    Resets conversation_windows (history + per-window metrics) and deletes
    the user_usage row so the user's 12-hour limits (requests, tokens,
    images, HTML files) are fully cleared.  Without the user_usage
    deletion, the user would still hit old limits after window cleanup.

    CRITICAL BUG FIX: The session is now completely removed from memory
    instead of just calling reset_usage(). This ensures that when the user
    sends their next message, a fresh session will be created with empty
    history loaded from the database. Previously, calling reset_usage()
    would clear the in-memory session but if the session wasn't recreated,
    stale history could remain in memory.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    target_uid = int(event.data.decode().split(":")[1])

    try:
        with self.db._get_conn() as conn:
            conn.execute(
                "UPDATE conversation_windows SET history = '[]', total_requests = 0, total_input_tokens = 0, total_output_tokens = 0 WHERE user_id = ?",
                (target_uid,)
            )
            # Also clear persistent 12-hour usage so limits are fully reset.
            conn.execute("DELETE FROM user_usage WHERE user_id = ?", (target_uid,))
            conn.commit()
    except Exception as e:
        tlogger.error(f"Failed to clear user windows in DB: {e}")
        await event.answer("خطا در پاکسازی پنجرهها!", alert=True)
        return

    # Remove the entire session from memory to force a fresh reload from DB
    # with empty history on the next user interaction.
    if target_uid in self.sessions:
        del self.sessions[target_uid]

    await event.answer("تمام پنجرهها و محدودیتهای مصرف کاربر پاکسازی شدند.", alert=True)
    from .commands import send_user_management_panel
    await send_user_management_panel(self, event, target_uid)


# ================================================================
# BLOCK MANAGEMENT
# ================================================================

async def admin_block_management(self: "TelegramBot", event: Any) -> None:
    """Show block management panel.

    Destination handler for cancel buttons in:
      - admin_block_user_start
      - admin_block_group_start

    delete_pending_state cleans up any leaked BLOCK_USER / BLOCK_GROUP
    pending state when the admin navigates back here.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(uid)
    blocked_users = self.db.get_blocked_users()
    blocked_groups = self.db.get_blocked_groups()

    text = "🚫 <b>مدیریت مسدود شدهها:</b>\n\n"
    buttons = []

    text += "👤 <b>کاربران مسدود شده:</b>\n"
    if not blocked_users:
        text += "❌ هیچ کاربری مسدود نشده است.\n"
    else:
        for bu in blocked_users:
            text += (
                f"▫️ <code>{bu['user_id']}</code>\n"
                f"  ┗ 📝 {bu.get('reason', 'بدون دلیل')}\n"
            )
            buttons.append([Button.inline(f"✅ آنبلاک {bu['user_id']}", f"admin_bl_unblock_user:{bu['user_id']}".encode(), style="success")])

    text += "\n👥 <b>گروههای مسدود شده:</b>\n"
    if not blocked_groups:
        text += "❌ هیچ گروهی مسدود نشده است.\n"
    else:
        for bg in blocked_groups:
            text += (
                f"▫️ <code>{bg['group_id']}</code>\n"
                f"  ┗ 📝 {bg.get('reason', 'بدون دلیل')}\n"
            )
            buttons.append([Button.inline(f"✅ آنبلاک {bg['group_id']}", f"admin_bl_unblock_group:{bg['group_id']}".encode(), style="success")])

    buttons.append([Button.inline("🚫 بلاک کاربر", b"admin_block_user_start", style="danger")])
    buttons.append([Button.inline("🚫 بلاک گروه", b"admin_block_group_start", style="danger")])
    buttons.append([Button.inline("🔙 بازگشت به پنل ادمین", b"admin_panel")])

    await event.edit(text, buttons=buttons, parse_mode="html")


async def admin_block_user_start(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_pending_state(uid, pending_question=False, pending_action="BLOCK_USER")
    msg = await event.edit(
        "✍️ <b>بلاک کردن کاربر:</b>\n\n"
        "لطفا آیدی عددی کاربر را ارسال کنید.\n\n"
        "میتوانید به صورت اختیاری دلیل بلاک را نیز با <code>|</code> جدا کنید:\n"
        "<code>آیدی عددی | دلیل بلاک</code>\n\n"
        "مثال:\n"
        "<code>123456789</code>\n"
        "یا:\n"
        "<code>123456789 | اسپم در گروه</code>",
        buttons=[[Button.inline("🔙 انصراف", b"admin_block_management")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "block_user")] = msg


async def admin_bl_unblock_user_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    target_uid = int(event.data.decode().split(":")[1])
    self.db.unblock_user(target_uid)
    await event.answer(f"کاربر {target_uid} با موفقیت آنبلاک شد.", alert=True)
    await admin_block_management(self, event)


async def admin_bl_unblock_group_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    group_id = int(event.data.decode().split(":")[1])
    self.db.unblock_group(group_id)
    await event.answer(f"گروه {group_id} با موفقیت آنبلاک شد.", alert=True)
    await admin_block_management(self, event)


async def admin_block_user_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    target_uid = int(event.data.decode().split(":")[1])
    self.db.block_user(target_uid, uid, "مسدود شده از پنل مدیریت کاربر")
    await event.answer(f"کاربر {target_uid} با موفقیت بلاک شد.", alert=True)
    from .commands import send_user_management_panel
    await send_user_management_panel(self, event, target_uid)


async def admin_unblock_user_cb(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی غیرمجاز!", alert=True)
        return

    target_uid = int(event.data.decode().split(":")[1])
    self.db.unblock_user(target_uid)
    await event.answer(f"کاربر {target_uid} با موفقیت آنبلاک شد.", alert=True)
    from .commands import send_user_management_panel
    await send_user_management_panel(self, event, target_uid)


async def admin_block_group_start(self: "TelegramBot", event: Any) -> None:
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.save_pending_state(uid, pending_question=False, pending_action="BLOCK_GROUP")
    msg = await event.edit(
        "✍️ <b>بلاک کردن گروه:</b>\n\n"
        "لطفا آیدی عددی گروه را ارسال کنید.\n\n"
        "میتوانید به صورت اختیاری دلیل بلاک را نیز با <code>|</code> جدا کنید:\n"
        "<code>آیدی عددی گروه | دلیل بلاک</code>\n\n"
        "مثال:\n"
        "<code>-100123456789</code>\n"
        "یا:\n"
        "<code>-100123456789 | استفاده غیرمجاز</code>",
        buttons=[[Button.inline("🔙 انصراف", b"admin_block_management")]],
        parse_mode="html"
    )
    self.pending_message[(uid, "block_group")] = msg


# ================================================================
# TOKEN LEADERS
# ================================================================

async def admin_token_leaders(self: "TelegramBot", event: Any) -> None:
    """Show top token consumers (users and groups).

    ``delete_pending_state`` cleans up any leaked pending state.
    """
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    self.db.delete_pending_state(uid)
    top_users = self.db.get_top_users_by_token(5)
    top_groups = self.db.get_top_groups_by_token(5)

    text = "🏆 <b>برترین مصرفکنندگان توکن</b>\n\n"

    text += "👤 <b>۵ کاربر برتر</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    if not top_users:
        text += "❌ دادهای وجود ندارد.\n"
    else:
        for i, user in enumerate(top_users, 1):
            uid_val = user["user_id"]
            tokens = user["total_tokens"]
            username_link = f"<a href='tg://user?id={uid_val}'>👤 {uid_val}</a>"
            text += (
                f"<b>{i}.</b> {username_link}\n"
                f"   ┣ 🆔 <code>{uid_val}</code>\n"
                f"   ┗ 🪙 <code>{tokens:,}</code> توکن\n\n"
            )

    text += "👥 <b>۵ گروه برتر</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    if not top_groups:
        text += "❌ دادهای وجود ندارد.\n"
    else:
        for i, group in enumerate(top_groups, 1):
            gid = group["group_id"]
            tokens = group["total_tokens"]
            text += (
                f"<b>{i}.</b> گروه <code>{gid}</code>\n"
                f"   ┗ 🪙 <code>{tokens:,}</code> توکن\n\n"
            )

    buttons = [
        [Button.inline("🔄 بروزرسانی", b"admin_token_leaders", style="primary")],
        [Button.inline("🔙 بازگشت به پنل ادمین", b"admin_panel")]
    ]

    await event.edit(text, buttons=buttons, parse_mode="html")


async def cw_classifier_model_start(self: "TelegramBot", event: Any) -> None:
    """Start setting the Channel Watcher classifier model."""
    uid = event.sender_id
    if uid not in self.admin_ids:
        await event.answer("دسترسی ادمین یافت نشد.", alert=True)
        return

    current = self.db.get_setting("cw_classifier_model", "gemini-3.1-flash-lite")
    self.db.save_pending_state(uid, pending_question=False, pending_action="CW_CLASSIFIER_MODEL")
    msg = await event.edit(
        f"🤖 <b>تغییر مدل Classifier پایش کانال:</b>\n\n"
        f"مدل فعلی: <code>{current}</code>\n\n"
        f"نام مدل جدید را ارسال کنید:\n"
        f"مثال: <code>gemini-3.1-flash-lite</code>\n"
        f"<code>gemini-3.5-flash</code>\n\n"
        f"یا انصراف دهید:",
        buttons=[[Button.inline("🔙 انصراف", b"admin_panel")]],
        parse_mode="html",
    )
    self.pending_message[(uid, "cw_classifier_model")] = msg
