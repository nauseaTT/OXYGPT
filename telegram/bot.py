"""Telegram bot orchestrator.

Manages the Telegram client lifecycle, user sessions, rate limiting,
event handler registration, and periodic metric resets. This is the
main entry point that wires together the AI service, database, and
all Telegram event handlers.
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Set, Optional, Any

# v2: Telethon v2 renamed `TelegramClient` -> `Client`, split events from
# filters, moved the raw API to the private `telethon._tl` (snake_case), and
# turned `telethon.errors` into a factory. All of that is funneled through the
# documented `telethon_compat` layer so this module keeps using familiar names
# (`TelegramClient`, `events`, `Button`) while running on v2. See
# MIGRATION_NOTES.md and telethon_compat.py.
from telethon_compat import (
    TelegramClient, events, Button, tl, filters,
    UserNotParticipantError,
    data_regex, text_regex, channel_ref_from_stored_id, user_ref,
)
from dotenv import load_dotenv

from api_http import AI_Service, get_service_manager
from database import DatabaseManager
from system_prompt import (
    micheal_prompt, daye_prompt, zeussy_prompt, albrooks_prompt,
)
from gemini_503_manager import init_global_503_manager, get_global_503_manager

from .utils import get_time_until_reset, get_last_reset_time
from .animator import StatusAnimator
from . import handlers as h

from skills import SKILLS, get_skill, get_skill_prompt

load_dotenv()

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone
    tz_tehran = timezone(timedelta(hours=3, minutes=30))
else:
    tz_tehran = ZoneInfo("Asia/Tehran")

os.makedirs("logs", exist_ok=True)
tlogger = logging.getLogger("telegram")
tlogger.setLevel(logging.INFO)
if not tlogger.handlers:
    file_handler = logging.FileHandler("logs/telegram.log", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    tlogger.addHandler(file_handler)


class TelegramBot:
    """Main Telegram bot class.

    Orchestrates the entire bot lifecycle: initializes the Telethon client,
    manages per-user AI_Service sessions, enforces rate limits (requests,
    tokens, images, HTML files), registers all event handlers, and runs
    a periodic 12-hour usage reset loop.

    Attributes:
        db: DatabaseManager instance for all persistence.
        bot: The underlying Telethon TelegramClient.
        sessions: Active AI_Service instances keyed by user_id.
        processing_users: Set of user IDs currently waiting for AI response.
        pending_message: Tracks bot messages awaiting user input (e.g., window title).
        previous_ai_messages: Tracks previous AI response messages per user for
            button management. When user enters waiting state, buttons on these
            messages are disabled (info-only). Structure: {uid: [(msg, buttons), ...]}.
        active_requests: Tracks in-flight AI generation tasks per user.
        failed_messages: Stores failed request info for retry functionality.
        collapsed_messages: Tracks collapsed group messages for expand/collapse UX.
        admin_ids: List of admin Telegram user IDs.
        mentor_prompts: Map of mentor key to their system prompt text.
    """

    def __init__(self, api_id: int, api_hash: str, bot_token: str) -> None:
        """Initialize the Telegram bot.

        Args:
            api_id: Telegram API ID from my.telegram.org.
            api_hash: Telegram API hash from my.telegram.org.
            bot_token: Bot token from @BotFather.
        """
        self.api_id: int = api_id
        self.api_hash: str = api_hash
        self.bot_token: str = bot_token

        self.db: DatabaseManager = DatabaseManager()
        # v2: `TelegramClient('bot', api_id, api_hash)` -> `Client(...)` (aliased).
        # `check_all_handlers=True` is REQUIRED here to preserve v1 behavior:
        # v1 ran every matching handler (there was no StopPropagation raised in
        # this codebase); v2 stops after the first handler whose filter returns
        # True unless this flag is set. Two catch-all NewMessage(incoming)
        # handlers (`pending_message_handler` + `inline_handler`) must both run
        # on the same message, so we opt into running all matching handlers.
        # (see migration guide: "Behaviour changes in events")
        from paths import session_name
        self.bot: TelegramClient = TelegramClient(
            session_name('bot'), self.api_id, self.api_hash, check_all_handlers=True
        )

        self.sessions: Dict[int, AI_Service] = {}
        self.processing_users: Set[int] = set()
        self.pending_message: Dict[Tuple[int, str], Any] = {}
        self.previous_ai_messages: Dict[int, List[Tuple[Any, List]]] = {}
        self.last_warning_time: Dict[int, datetime] = {}
        self.active_requests: Dict[int, Dict[str, Any]] = {}
        self.failed_messages: Dict[int, Dict[str, Any]] = {}
        self.collapsed_messages: Dict[int, Dict[str, Any]] = {}
        self.last_tokens: Dict[int, int] = {}
        self.last_hook: Dict[int, str] = {}

        # Membership cache: {user_id: (cached_until_timestamp, not_joined_locks)}
        # Cached for 300 seconds (5 minutes) to avoid redundant API calls.
        self._membership_cache: Dict[int, tuple] = {}

        self._agg_cache: Optional[Dict[str, Any]] = None
        self._agg_cache_time: Optional[datetime] = None
        self._classifier_service: Optional[AI_Service] = None

        # Load admin IDs from environment variable
        admin_ids_str = os.environ.get("ADMIN_IDS", "")
        if admin_ids_str:
            self.admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().lstrip('-').isdigit()]
        else:
            admin_user_id = int(os.environ.get("ADMIN_USER_ID", "0"))
            self.admin_ids = [admin_user_id] if admin_user_id else [8071301975]
        self.mentor_prompts: Dict[str, str] = {
            "micheal": micheal_prompt,
            "daye": daye_prompt,
            "zeussy": zeussy_prompt,
            "albrooks": albrooks_prompt,
        }
        self.inline_article: List[Tuple[str, str, str, str]] = [
            ("Micheal Huddleston", "soon...", "\u200bmicheal", "A guy opens a Buy trade and gets liquidated, and you think that of me?\nNo. I'm the one who opened the sell"),
            ("Daye", "soon...", "\u200bdaye", "i have smt between my legs, wanna see it?"),
            ("Zeussy", "soon...", "\u200bzeussy", "the market has no effect on you? Who Decided That"),
            ("Albrooks", "soon", "\u200balbrooks", "Liquidity provider, nice to meet you")
        ]

        # Initialize 503 manager as singleton
        admin_id = int(os.environ.get("ADMIN_USER_ID", "0"))
        if admin_id:
            init_global_503_manager(self.db, admin_id)
            tlogger.info(f"[503 MANAGER] Initialized with admin_id={admin_id}")
        else:
            tlogger.warning("[503 MANAGER] ADMIN_USER_ID not set in .env - notifications disabled")

        self._bind_handlers()
        self._register_handlers()

    def _bind_handlers(self) -> None:
        """Bind handler functions from the handlers package as instance methods.

        Maps handler names to their functions and attaches them to this instance
        using descriptor protocol, so they can be used as self.method_name in
        event registrations.
        """
        _handler_funcs = {
            "start": h.start,
            "cancel_command": h.cancel_command,
            "arise_command": h.arise_command,
            "check_membership_callback": h.check_membership_callback,
            "send_user_management_panel": h.send_user_management_panel,
            "admin_panel": h.admin_panel,
            "admin_refresh": h.admin_refresh,
            "admin_reset": h.admin_reset,
            "admin_export": h.admin_export,
            "admin_models": h.admin_models,
            "admin_locks": h.admin_locks,
            "add_lock_start": h.add_lock_start,
            "delete_lock": h.delete_lock,
            "change_model_menu": h.change_model_menu,
            "set_model_value": h.set_model_value,
            "admin_toggle_sub_cb": h.admin_toggle_sub_cb,
            "admin_reset_user_limit_cb": h.admin_reset_user_limit_cb,
            "admin_clear_user_windows_cb": h.admin_clear_user_windows_cb,
            "admin_block_management": h.admin_block_management,
            "admin_block_user_start": h.admin_block_user_start,
            "admin_block_user_cb": h.admin_block_user_cb,
            "admin_unblock_user_cb": h.admin_unblock_user_cb,
            "admin_bl_unblock_user_cb": h.admin_bl_unblock_user_cb,
            "admin_bl_unblock_group_cb": h.admin_bl_unblock_group_cb,
            "admin_block_group_start": h.admin_block_group_start,
            "admin_token_leaders": h.admin_token_leaders,
            "admin_providers": h.admin_providers,
            "set_provider_cb": h.set_provider_cb,
            "admin_openai_config": h.admin_openai_config,
            "openai_set_base_url_start": h.openai_set_base_url_start,
            "openai_add_key_start": h.openai_add_key_start,
            "openai_remove_key_menu": h.openai_remove_key_menu,
            "openai_remove_key_confirm": h.openai_remove_key_confirm,
            "openai_remove_all_keys": h.openai_remove_all_keys,
            "openai_custom_model_start": h.openai_custom_model_start,
            "quickask_cb": h.quickask_cb,
            "skill_toggle_cb": h.skill_toggle_cb,
            "skill_send_message_cb": h.skill_send_message_cb,
            "mentors_cb": h.mentors_cb,
            "placeholder_cb": h.placeholder_cb,
            "limit_countdown_cb": h.limit_countdown_cb,
            "help_menu_cb": h.help_menu_cb,
            "generic_callback": h.generic_callback,
            "back_to_main_cb": h.back_to_main_cb,
            "manage_windows_cb": h.manage_windows_cb,
            "create_window_start_cb": h.create_window_start_cb,
            "set_active_win_cb": h.set_active_win_cb,
            "delete_win_req_cb": h.delete_win_req_cb,
            "delete_win_confirm_cb": h.delete_win_confirm_cb,
            "clear_win_req_cb": h.clear_win_req_cb,
            "clear_win_confirm_cb": h.clear_win_confirm_cb,
            "rename_win_start_cb": h.rename_win_start_cb,
            "pending_message_handler": h.pending_message_handler,
            "retry_failed_handler": h.retry_failed_handler,
            "inline_handler": h.inline_handler,
            "again_talk_mentor": h.again_talk_mentor,
            "again_talk_quickask": h.again_talk_quickask,
            "clear_processing": h.clear_processing,
            "select_window_panel_handler": h.select_window_panel_handler,
            "switch_window_inline_handler": h.switch_window_inline_handler,
            "inline_query": h.inline_query,
            "expand_cb": h.expand_cb,
            "auto_collapse_toggle_cb": h.auto_collapse_toggle_cb,
            "windows_cmd": h.windows_cmd,
            "switch_cmd": h.switch_cmd,
            "new_window_cmd": h.new_window_cmd,
            "clear_cmd": h.clear_cmd,
            "clear_win_shortcut_cb": h.clear_win_shortcut_cb,
            "cancel_pending_cb": h.cancel_pending_cb,
            "ask_cmd": h.ask_cmd,
            "learn_cmd": h.learn_cmd,
            "code_cmd": h.code_cmd,
            "deep_cmd": h.deep_cmd,
            "micheal_cmd": h.micheal_cmd,
            "daye_cmd": h.daye_cmd,
            "zeussy_cmd": h.zeussy_cmd,
            "albrooks_cmd": h.albrooks_cmd,
            "status_cmd": h.status_cmd,
            "help_cmd": h.help_cmd,
            "openai_remove_all_keys_confirm": h.openai_remove_all_keys_confirm,
            "provider_test_connection": h.provider_test_connection,
            "provider_token_dashboard": h.provider_token_dashboard,
            "admin_services": h.admin_services,
            "service_create_start": h.service_create_start,
            "service_edit": h.service_edit,
            "service_delete_confirm": h.service_delete_confirm,
            "service_delete": h.service_delete,
            "service_test": h.service_test,
            "service_set_url_start": h.service_set_url_start,
            "service_add_key_start": h.service_add_key_start,
            "service_set_model_start": h.service_set_model_start,
            "service_remove_key_menu": h.service_remove_key_menu,
            "service_remove_key_confirm": h.service_remove_key_confirm,
            "activate_service": h.activate_service,
            "deactivate_service": h.deactivate_service,
            "cw_classifier_model_start": h.cw_classifier_model_start,
            "verify_qa_continue_cb": h.verify_qa_continue_cb,
            "verify_qa_switch_cb": h.verify_qa_switch_cb,
            "verify_qa_new_cb": h.verify_qa_new_cb,
            "verify_qa_delete_req_cb": h.verify_qa_delete_req_cb,
            "verify_qa_delete_confirm_cb": h.verify_qa_delete_confirm_cb,
            "verify_qa_cancel_delete_cb": h.verify_qa_cancel_delete_cb,
            "verify_mentor_continue_cb": h.verify_mentor_continue_cb,
            "verify_mentor_reset_cb": h.verify_mentor_reset_cb,
            "support_entry_cb": h.support_entry_cb,
            "support_cmd": h.support_cmd,
            "support_suggested_cb": h.support_suggested_cb,
            "support_continue_cb": h.support_continue_cb,
            "support_exit_cb": h.support_exit_cb,
            "admin_toggle_support_cb": h.admin_toggle_support_cb,
        }
        for name, func in _handler_funcs.items():
            setattr(self, name, func.__get__(self, type(self)))

    def get_user_subscription(self, uid: int) -> str:
        """Get user's subscription tier.

        Args:
            uid: Telegram user ID.

        Returns:
            Subscription type string: "free" or "paid".
        """
        return self.db.get_setting(f"sub_{uid}", "free")

    def set_user_subscription(self, uid: int, sub_type: str) -> None:
        """Set user's subscription tier (admin operation).

        Args:
            uid: Telegram user ID.
            sub_type: Subscription type to set ("free" or "paid").
        """
        self.db.save_setting(f"sub_{uid}", sub_type)

    def check_user_limits(self, uid: int) -> Tuple[bool, str]:
        """Check if user has exceeded their request or token limits.

        Limits (per 12-hour window):
            - Free: 25 requests, 150k tokens
            - Paid: 760 requests, 300k tokens

        Args:
            uid: Telegram user ID.

        Returns:
            Tuple of (is_limited, error_message). If not limited, error_message is empty.
        """
        sub_type = self.get_user_subscription(uid)
        usage = self.db.get_user_usage(uid)
        user_total_requests = usage["total_requests"]
        user_total_tokens = usage["total_input_tokens"] + usage["total_output_tokens"]

        if sub_type == "paid":
            max_requests = 760
            max_tokens = 300000
        else:
            max_requests = 25
            max_tokens = 150000

        time_left = get_time_until_reset()
        if user_total_requests >= max_requests:
            return True, f"❌ شما به سقف مجاز تعداد پیام‌های اشتراک خود ({max_requests} پیام) رسیده‌اید.\n⏳ زمان باقی‌مانده تا ریست شدن محدودیت‌ها: {time_left}"
        if user_total_tokens >= max_tokens:
            return True, f"❌ شما به سقف مجاز توکن‌های اشتراک خود ({max_tokens:,} توکن) رسیده‌اید.\n⏳ زمان باقی‌مانده تا ریست شدن محدودیت‌ها: {time_left}"

        return False, ""

    def check_user_80_percent_limit(self, uid: int, trigger_alert: bool = False) -> Tuple[bool, str]:
        """Check if user has reached 80% of their limits (soft warning).

        When trigger_alert is True, records the warning timestamp to avoid
        repeated alerts within the same reset window.

        Args:
            uid: Telegram user ID.
            trigger_alert: If True, persist the warning timestamp.

        Returns:
            Tuple of (is_near_limit, warning_message).
        """
        sub_type = self.get_user_subscription(uid)
        usage = self.db.get_user_usage(uid)
        user_total_requests = usage["total_requests"]
        user_total_tokens = usage["total_input_tokens"] + usage["total_output_tokens"]

        if sub_type == "paid":
            max_requests = 760
            max_tokens = 300000
        else:
            max_requests = 25
            max_tokens = 150000

        req_80 = max_requests * 0.8
        tok_80 = max_tokens * 0.8

        if user_total_requests >= req_80 or user_total_tokens >= tok_80:
            if trigger_alert:
                last_reset = get_last_reset_time()
                last_warn_str = self.db.get_setting(f"warned_80_time_{uid}", "")
                if last_warn_str:
                    try:
                        last_warn = datetime.fromisoformat(last_warn_str)
                        if last_warn >= last_reset:
                            return False, ""
                    except Exception:
                        pass
                self.db.save_setting(f"warned_80_time_{uid}", datetime.now(tz_tehran).isoformat())
            return True, "⚠️ شما به ۸۰٪ از حد مجاز مصرف خود رسیده‌اید! پیشنهاد می‌شود پنجره‌های با مکالمه طولانی‌مدت را پاکسازی یا حذف کنید، زیرا تاریخچه طولانی باعث مصرف سریع‌تر توکن‌ها می‌شود."
        return False, ""

    def check_image_limit(self, uid: int) -> Tuple[bool, str]:
        """
        بررسی محدودیت تولید تصویر.
        رایگان: 15 تصویر در 12 ساعت
        پولی: 40 تصویر در 12 ساعت
        """
        sub_type = self.get_user_subscription(uid)
        image_count = self.db.get_image_usage(uid)
        max_images = 40 if sub_type == "paid" else 15

        if image_count >= max_images:
            time_left = get_time_until_reset()
            return True, (
                f"❌ شما به سقف مجاز تصاویر ({max_images} تصویر) رسیده‌اید.\n"
                f"⏳ زمان باقی‌مانده تا ریست شدن: {time_left}"
            )
        return False, ""

    def check_html_limit(self, uid: int) -> Tuple[bool, str]:
        """
        بررسی محدودیت تولید فایل HTML.
        رایگان: 10 فایل در 12 ساعت
        پولی: 25 فایل در 12 ساعت
        """
        sub_type = self.get_user_subscription(uid)
        html_count = self.db.get_html_usage(uid)
        max_html = 25 if sub_type == "paid" else 10

        if html_count >= max_html:
            time_left = get_time_until_reset()
            return True, (
                f"❌ شما به سقف مجاز فایلهای HTML ({max_html} فایل) رسیدهاید.\n"
                f"⏳ زمان باقیمانده تا ریست شدن: {time_left}"
            )
        return False, ""

    async def send_limit_notification(
        self, 
        event: Any,
        limit_msg: str,
        is_callback: bool = False
    ) -> None:
        """
        ارسال نوتیفیکیشن limit به کاربر با روش مناسب.
        
        - اگر از دکمه شیشهای (callback): پاپآپ + ویرایش پیام
        - اگر از کامند: reply با دکمه countdown قابل کلیک
        
        Args:
            event: Telegram event (message or callback query)
            limit_msg: متن خطای limit
            is_callback: True اگر از دکمه شیشهای صدا زده شده
        """
        time_left = get_time_until_reset()
        
        if is_callback:
            # حالت دکمه شیشهای: پاپآپ + ویرایش پیام
            await event.answer(
                f"❌ شما به سقف مجاز رسیدهاید!\n⏳ {time_left} تا ریست", 
                alert=True
            )
            await event.edit(
                limit_msg,
                buttons=[[Button.inline("🔙 بازگشت به منوی اصلی", b"back_to_main")]]
            )
        else:
            # حالت کامند: reply با دکمه countdown قابل کلیک
            buttons = [[Button.inline(f"⏳ {time_left} تا ریست", b"limit_countdown")]]
            await event.reply(limit_msg, buttons=buttons)

    def invalidate_sessions_after_provider_change(self) -> int:
        """Clear in-memory AI_Service sessions after a global provider switch.

        When the admin changes the active provider (e.g. Gemini → OpenAI or
        vice versa), existing ``AI_Service`` instances in ``self.sessions``
        still hold the old provider's state (``self.provider``, ``self.service_id``,
        client references, etc.).  The next time each user sends a message,
        ``get_ai_service()`` creates a fresh instance — but until that happens,
        any in-flight or cached session is stale.

        This method drops all cached sessions so the next request forces a
        clean ``AI_Service`` construction that picks up the new provider from
        the database.

        Returns:
            Number of sessions cleared.
        """
        count = len(self.sessions)
        self.sessions.clear()
        self._agg_cache = None
        tlogger.info(f"Provider change: cleared {count} in-memory AI sessions")
        return count

    def get_ai_service(self, user_id: int, mode: str = "quick_ask", mentor_key: Optional[str] = None, chat_id: Optional[int] = None) -> AI_Service:
        """Get or create an AI_Service instance for the user's active window.

        Finds or creates a conversation window matching the requested mode/mentor,
        sets it as active, and initializes a fresh AI_Service session.

        Args:
            user_id: Telegram user ID.
            mode: Conversation mode ("quick_ask" or "mentor").
            mentor_key: Mentor identifier (required if mode is "mentor").
            chat_id: Telegram chat ID for group token tracking.

        Returns:
            A configured AI_Service instance.
        """
        active_win = self.db.get_active_window(user_id, default_mode=mode, default_mentor=mentor_key)
        if active_win["mode"] != mode or active_win["mentor_key"] != mentor_key:
            windows = self.db.get_user_windows(user_id)
            matching_win = next((w for w in windows if w["mode"] == mode and w["mentor_key"] == mentor_key), None)
            if matching_win:
                self.db.set_active_window(user_id, matching_win["window_id"])
            else:
                if mode == "quick_ask":
                    title = "سوال سریع"
                elif mode == "support":
                    title = "🛟 پشتیبان هوشمند"
                else:
                    title = f"منتور {mentor_key}"
                self.db.create_window(user_id, title, mode=mode, mentor_key=mentor_key)
                windows = self.db.get_user_windows(user_id)
                new_win = next((w for w in windows if w["mode"] == mode and w["mentor_key"] == mentor_key), None)
                if new_win:
                    self.db.set_active_window(user_id, new_win["window_id"])
            # Re-fetch the active window after changes
            active_win = self.db.get_active_window(user_id, default_mode=mode, default_mentor=mentor_key)

        # Get service_id from active window
        service_id = active_win.get("service_id") if active_win else None

        # If no service assigned to this window, apply the global default
        if not service_id:
            from api_http import get_active_provider
            if get_active_provider(self.db) == "openai":
                default_svc = self.db.get_setting("default_openai_service", "")
                if default_svc and get_service_manager(self.db).has_service(default_svc):
                    service_id = default_svc
                    if active_win:
                        self.db.set_window_service(active_win["window_id"], service_id)

        self.sessions[user_id] = AI_Service(
            user_id, db_manager=self.db, mode=mode, 
            mentor_key=mentor_key, chat_id=chat_id, service_id=service_id
        )
        return self.sessions[user_id]

    def get_classifier_service(self) -> AI_Service:
        """Create an AI service for Channel Watcher classifier.

        Uses ``user_id=0`` (system) so usage is never counted toward any
        user's quota.  The model name is read from the DB setting
        ``cw_classifier_model`` (default ``gemini-3.1-flash-lite``) and
        can be changed dynamically via the admin panel.
        """
        if self._classifier_service is not None:
            return self._classifier_service
        from api_http import get_active_provider
        model = self.db.get_setting("cw_classifier_model", "gemini-3.1-flash-lite")
        provider = get_active_provider(self.db)
        service_id = None
        if provider == "openai":
            service_id = self.db.get_setting("default_openai_service", "")
        svc = AI_Service(0, db_manager=self.db, mode="quick_ask", service_id=service_id)
        svc.fallback_model = model
        self._classifier_service = svc
        return svc

    def check_user_blocked(self, user_id: int) -> bool:
        """Check if a user is blocked by an admin."""
        return self.db.is_user_blocked(user_id)

    def check_group_blocked(self, chat_id: int) -> bool:
        """Check if a group chat is blocked by an admin."""
        return self.db.is_group_blocked(chat_id)

    async def check_user_joined(self, user_id: int) -> List[Dict[str, Any]]:
        """Check if user has joined all required channels/groups.

        Uses ``asyncio.gather`` to run all membership checks concurrently
        instead of sequentially, reducing total latency from O(n) to O(1)
        (Bug #32).  Results are cached for 300 seconds to avoid redundant
        API calls on every message (Bug #33).

        Admins are exempt from lock checks.

        Args:
            user_id: Telegram user ID to verify.

        Returns:
            List of lock dicts the user has NOT joined. Empty if all joined.
        """
        if user_id in self.admin_ids:
            return []

        # Membership cache (300s TTL)
        now_ts = datetime.now().timestamp()
        cached = self._membership_cache.get(user_id)
        if cached:
            expire_at, not_joined = cached
            if now_ts < expire_at:
                return not_joined

        locks = self.db.get_locks()
        if not locks:
            self._membership_cache[user_id] = (now_ts + 300, [])
            return []

        async def _check_one(lock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            try:
                # v2: v1 `client(GetParticipantRequest(channel=, participant=))`
                # (public raw request) -> the raw API is now private and
                # snake_case: `tl.functions.channels.get_participant(...)`. It
                # takes input-peer objects, so we build them from the stored ids
                # via PeerRef helpers (the entity cache that v1 relied on no
                # longer exists in v2). A single-participant lookup keeps the
                # original O(1)-per-lock cost. `UserNotParticipant` is raised
                # when the user is not a member.
                chan_ref = channel_ref_from_stored_id(lock["channel_id"])
                await self.bot(tl.functions.channels.get_participant(
                    # get_participant wants an InputChannel for `channel` and an
                    # InputPeer for `participant`; ChannelRef/UserRef expose the
                    # right low-level constructors.
                    channel=chan_ref._to_input_channel(),
                    participant=user_ref(user_id)._to_input_peer(),
                ))
                self.db.log_join(user_id, lock["channel_id"])
                return None  # joined
            except UserNotParticipantError:
                return lock  # not joined
            except Exception as e:
                tlogger.warning(f"Could not check membership for user {user_id} in {lock['channel_id']}: {e}")
                return None  # treat errors as "joined" (avoid false positives)

        results = await asyncio.gather(*[_check_one(lock) for lock in locks])
        not_joined = [r for r in results if r is not None]

        # Cache result for 300 seconds
        self._membership_cache[user_id] = (now_ts + 300, not_joined)
        return not_joined

    async def send_join_warning(self, event: Any, not_joined_locks: List[Dict[str, Any]]) -> None:
        """Send a membership requirement warning with join buttons.

        Rate-limited to once per 60 seconds per user to avoid spam.
        In groups, the warning auto-deletes after 120 seconds.

        Args:
            event: The incoming Telegram event.
            not_joined_locks: List of lock dicts the user hasn't joined.
        """
        uid = event.sender_id
        now = datetime.now()

        last_sent = self.last_warning_time.get(uid)
        if last_sent and (now - last_sent).total_seconds() < 60:
            return

        self.last_warning_time[uid] = now

        buttons = []
        for lock in not_joined_locks:
            buttons.append([Button.url(f"📢 {lock['title']}", lock['invite_link'])])
        buttons.append([Button.inline("✅ عضو شدم", b"check_membership")])

        text = (
            "⚠️ <b>برای استفاده از ربات، ابتدا باید عضو کانال‌ها یا گروه‌های زیر شوید:</b>\n\n"
            "پس از عضویت، روی دکمه <b>«عضو شدم»</b> کلیک کنید تا ربات برای شما فعال شود."
        )

        msg = None
        # v2: `events.CallbackQuery.Event` -> `events.ButtonCallback` (exposed
        # via the compat facade). Callback events have no reply target, so they
        # `respond` (send a new message); message events `reply`. Both accept
        # the v1 `parse_mode=`/`buttons=` kwargs via the compat wrappers.
        if isinstance(event, events.ButtonCallback):
            msg = await event.respond(text, buttons=buttons, parse_mode="html")
        else:
            msg = await event.reply(text, buttons=buttons, parse_mode="html")

        if msg and not event.is_private:
            async def delete_after_delay(m, delay):
                await asyncio.sleep(delay)
                try:
                    await m.delete()
                except Exception:
                    pass
            asyncio.create_task(delete_after_delay(msg, 120))

    def aggregate_api_status(self) -> Dict[str, Any]:
        """Aggregate system-wide API usage statistics."""
        from api_http import get_active_provider, AI_MODEL_SEARCH, AI_MODEL_FALLBACK, OPENAI_MODEL_FALLBACK

        now = datetime.now()
        if self._agg_cache and self._agg_cache_time and (now - self._agg_cache_time).total_seconds() < 30:
            return self._agg_cache

        provider = get_active_provider(self.db)

        if provider == "openai":
            agg = {
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "history_messages_count": 0,
                "instances": 0,
                "search_model": self.db.get_setting("search_model", AI_MODEL_SEARCH),
                "fallback_model": self.db.get_setting("openai_fallback_model", OPENAI_MODEL_FALLBACK),
                "quick_ask_model": self.db.get_setting("openai_quick_ask_model", OPENAI_MODEL_FALLBACK),
                "mentors_model": self.db.get_setting("openai_mentors_model", OPENAI_MODEL_FALLBACK),
                "downgrade_active": False,
                "provider": provider
            }
        else:
            agg = {
                "total_requests": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "history_messages_count": 0,
                "instances": 0,
                "search_model": self.db.get_setting("search_model", AI_MODEL_SEARCH),
                "fallback_model": self.db.get_setting("fallback_model", AI_MODEL_FALLBACK),
                "quick_ask_model": self.db.get_setting("quick_ask_model", AI_MODEL_FALLBACK),
                "mentors_model": self.db.get_setting("mentors_model", AI_MODEL_FALLBACK),
                "downgrade_active": False,
                "provider": provider
            }

        try:
            with self.db._get_conn() as conn:
                rows = [dict(r) for r in conn.execute("SELECT * FROM conversation_windows").fetchall()]
        except Exception as e:
            tlogger.error(f"Error fetching windows for aggregation: {e}")
            rows = []

        active_users = set()
        for row in rows:
            try:
                user_id = row.get("user_id")
                history_str = row.get("history")
                total_requests = row.get("total_requests") or 0
                total_input_tokens = row.get("total_input_tokens") or 0
                total_output_tokens = row.get("total_output_tokens") or 0

                history = json.loads(history_str or "[]")
                agg["total_requests"] += total_requests
                agg["total_input_tokens"] += total_input_tokens
                agg["total_output_tokens"] += total_output_tokens
                agg["history_messages_count"] += len(history)
                if user_id:
                    active_users.add(user_id)
            except Exception as e:
                tlogger.error(f"'aggregate_api_status' row processing issue: {e}")
                continue

        agg["instances"] = len(active_users)
        agg["total_tokens"] = agg["total_input_tokens"] + agg["total_output_tokens"]

        if agg["total_requests"] >= 1000 or agg["total_tokens"] >= 5000000:
            agg["downgrade_active"] = True

        self._agg_cache = agg
        self._agg_cache_time = now
        return agg

    def format_status_report(self, agg: Dict[str, Any]) -> str:
        """Format aggregated API stats into a Telegram HTML status report."""
        from api_http import get_active_provider
        provider = get_active_provider(self.db)
        downgrade_status = "⚠️ فعال (تنزل به 3.1-flash-lite)" if agg["downgrade_active"] else "✅ عادی"
        provider_icon = "🟢" if provider == "gemini" else "🔵"
        return (
            f"📊 <b>گزارش وضعیت سیستم و API:</b>\n\n"
            f"{provider_icon} <b>Provider:</b> <code>{provider}</code>\n\n"
            f"🔹 درخواستها: <code>{agg['total_requests']}</code>\n"
            f"🔹 توکن ورودی: <code>{agg['total_input_tokens']}</code>\n"
            f"🔹 توکن خروجی: <code>{agg['total_output_tokens']}</code>\n"
            f"🔹 مجموع توکنها: <code>{agg['total_tokens']}</code>\n"
            f"🔹 پیامهای ذخیرهشده: <code>{agg['history_messages_count']}</code>\n"
            f"🔹 کاربران فعال: <code>{agg['instances']}</code>\n\n"
            f"⚙️ <b>مدلهای فعال:</b>\n"
            f"🔍 سرچ وب: <code>{agg['search_model']}</code>\n"
            f"⚡️ سوال سریع: <code>{agg['quick_ask_model']}</code>\n"
            f"🎓 منتورها: <code>{agg['mentors_model']}</code>\n"
            f"⚙️ مدل پشتیبان: <code>{agg['fallback_model']}</code>\n\n"
            f"🛡 <b>وضعیت محدودیت (80%):</b> {downgrade_status}\n"
        )

    def _register_handlers(self) -> None:
        """Register all Telegram event handlers with the Telethon client.

        Wires up command handlers (/start, /help, etc.), callback query
        handlers (inline buttons), new message handlers (AI processing),
        and inline query handlers. Handler order matters: specific patterns
        are registered before catch-all handlers.

        v2 registration model (see MIGRATION_NOTES.md sec. 4):
          * `client.on(event_cls, filter)` — events and filters are now
            SEPARATE. The event class is what the handler receives; the filter
            is a standalone predicate (combinable with `&`, `|`, `~`).
          * v1 `events.NewMessage(incoming=True, pattern=P)` becomes
            `events.NewMessage, filters.Incoming() & text_regex(P)`.
            `text_regex` (compat) uses `re.match` to reproduce v1's
            START-anchored matching exactly — v2's built-in `filters.Text`
            uses `re.search` and would over-match. This preserves the
            non-ASCII `اکسی` trigger and prefix shortcuts like `/w`, `/sw`.
          * v1 `events.CallbackQuery(data=b"D")` becomes
            `events.ButtonCallback, filters.Data(b"D")` (exact bytes match).
          * v1 `events.CallbackQuery(pattern=P)` becomes
            `events.ButtonCallback, data_regex(P)` (compat) which runs
            `re.match(P, data.decode())`, reproducing v1's prefix/lookahead
            matching. Registration ORDER is preserved verbatim so the
            negative-lookahead prefix-collision cases below still work.
          * v2 renamed `events.CallbackQuery` -> `events.ButtonCallback` and it
            no longer also fires for inline callbacks (a v1 hack); this bot has
            no inline callback buttons, so that change is a no-op here.
          * The two catch-all `events.NewMessage(incoming=True)` handlers
            (`pending_message_handler`, `inline_handler`) both need to run; the
            client was created with `check_all_handlers=True` (see __init__).
        """
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/start|اکسی"))(self.start)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/cancel"))(self.cancel_command)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/arise"))(self.arise_command)

        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/w"))(self.windows_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/sw"))(self.switch_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/new"))(self.new_window_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/clear"))(self.clear_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/ask"))(self.ask_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/learn"))(self.learn_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/code"))(self.code_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/deep"))(self.deep_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/micheal"))(self.micheal_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/daye"))(self.daye_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/zeussy"))(self.zeussy_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/albrooks"))(self.albrooks_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/status"))(self.status_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/help"))(self.help_cmd)
        self.bot.on(events.NewMessage, filters.Incoming() & text_regex("/support"))(self.support_cmd)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_panel"))(self.admin_panel)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_refresh"))(self.admin_refresh)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_reset"))(self.admin_reset)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_export"))(self.admin_export)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_models"))(self.admin_models)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_locks"))(self.admin_locks)
        self.bot.on(events.ButtonCallback, filters.Data(b"add_lock_start"))(self.add_lock_start)
        self.bot.on(events.ButtonCallback, data_regex(r"delete_lock:"))(self.delete_lock)
        self.bot.on(events.ButtonCallback, filters.Data(b"check_membership"))(self.check_membership_callback)
        self.bot.on(events.ButtonCallback, data_regex(r"change_model:"))(self.change_model_menu)
        self.bot.on(events.ButtonCallback, data_regex(r"set_model:"))(self.set_model_value)
        self.bot.on(events.ButtonCallback, filters.Data(b"quickask"))(self.quickask_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"skill_toggle:"))(self.skill_toggle_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"skill_send_message"))(self.skill_send_message_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"mentors"))(self.mentors_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"place_holder"))(self.placeholder_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"limit_countdown"))(self.limit_countdown_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"help_menu"))(self.help_menu_cb)

        self.bot.on(events.ButtonCallback, filters.Data(b"manage_windows"))(self.manage_windows_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"create_window_start"))(self.create_window_start_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"set_active_win:"))(self.set_active_win_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"delete_win_req:"))(self.delete_win_req_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"delete_win_confirm:"))(self.delete_win_confirm_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"clear_win_req:"))(self.clear_win_req_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"clear_win_confirm:"))(self.clear_win_confirm_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"clear_win_shortcut:"))(self.clear_win_shortcut_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"cancel_pending"))(self.cancel_pending_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"rename_win_start:"))(self.rename_win_start_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"back_to_main"))(self.back_to_main_cb)

        # AI Support Assistant (see telegram/handlers/support.py). v2 style:
        # exact-bytes callbacks use filters.Data(...); prefixed callbacks
        # ("support_suggested:<idx>" and "Asupport_<uid>") use data_regex(...),
        # the compat re.search-based matcher that mirrors v1's `pattern=`.
        self.bot.on(events.ButtonCallback, filters.Data(b"support_entry"))(self.support_entry_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"support_exit"))(self.support_exit_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"support_suggested:"))(self.support_suggested_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"Asupport_"))(self.support_continue_cb)

        self.bot.on(events.ButtonCallback, data_regex(r"^mentor_"))(self.generic_callback)
        self.bot.on(events.NewMessage, filters.Incoming())(self.pending_message_handler)
        self.bot.on(events.NewMessage, filters.Incoming())(self.inline_handler)
        self.bot.on(events.ButtonCallback, data_regex("Amentors_"))(self.again_talk_mentor)
        self.bot.on(events.ButtonCallback, data_regex("Aquickask_"))(self.again_talk_quickask)
        self.bot.on(events.ButtonCallback, data_regex("clear_processing"))(self.clear_processing)

        self.bot.on(events.ButtonCallback, data_regex(r"retry_failed:"))(self.retry_failed_handler)
        self.bot.on(events.ButtonCallback, data_regex(r"select_window_panel:"))(self.select_window_panel_handler)
        self.bot.on(events.ButtonCallback, data_regex(r"switch_window_inline:"))(self.switch_window_inline_handler)

        self.bot.on(events.ButtonCallback, data_regex(r"expand:"))(self.expand_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"auto_collapse_toggle:"))(self.auto_collapse_toggle_cb)

        self.bot.on(events.ButtonCallback, data_regex(r"admin_toggle_sub:"))(self.admin_toggle_sub_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"admin_reset_user_limit:"))(self.admin_reset_user_limit_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"admin_clear_user_windows:"))(self.admin_clear_user_windows_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_block_management"))(self.admin_block_management)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_block_user_start"))(self.admin_block_user_start)
        self.bot.on(events.ButtonCallback, data_regex(r"admin_block_user:"))(self.admin_block_user_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"admin_unblock_user:"))(self.admin_unblock_user_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"admin_bl_unblock_user:"))(self.admin_bl_unblock_user_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"admin_bl_unblock_group:"))(self.admin_bl_unblock_group_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_block_group_start"))(self.admin_block_group_start)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_token_leaders"))(self.admin_token_leaders)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_toggle_support"))(self.admin_toggle_support_cb)

        # Provider management handlers
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_providers"))(self.admin_providers)
        self.bot.on(events.ButtonCallback, data_regex(r"set_provider:"))(self.set_provider_cb)
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_openai_config"))(self.admin_openai_config)
        self.bot.on(events.ButtonCallback, filters.Data(b"openai_set_base_url"))(self.openai_set_base_url_start)
        self.bot.on(events.ButtonCallback, filters.Data(b"openai_add_key"))(self.openai_add_key_start)
        self.bot.on(events.ButtonCallback, filters.Data(b"openai_remove_key"))(self.openai_remove_key_menu)
        self.bot.on(events.ButtonCallback, data_regex(r"openai_remove_key_confirm:"))(self.openai_remove_key_confirm)
        self.bot.on(events.ButtonCallback, filters.Data(b"openai_remove_all_keys_confirm"))(self.openai_remove_all_keys_confirm)
        self.bot.on(events.ButtonCallback, filters.Data(b"openai_remove_all_keys"))(self.openai_remove_all_keys)
        self.bot.on(events.ButtonCallback, data_regex(r"openai_custom_model_start:"))(self.openai_custom_model_start)
        self.bot.on(events.ButtonCallback, filters.Data(b"provider_test_connection"))(self.provider_test_connection)
        self.bot.on(events.ButtonCallback, filters.Data(b"provider_token_dashboard"))(self.provider_token_dashboard)

        # Service management handlers
        #
        # ⚠️  ORDER & REGEX SENSITIVITY
        # Telethon uses re.match() — which anchors at the start of the string.
        # A pattern like "service_delete:" would ALSO match
        # "service_delete_confirm:nvidia" because "service_delete:"
        # is a literal prefix of "service_delete_confirm:nvidia".
        #
        # We register service_delete_confirm FIRST (line 677), then use a
        # negative lookahead (?!confirm) on service_delete (line 678) so that
        # data starting with "service_delete_confirm:" ONLY fires
        # service_delete_confirm, NOT service_delete.
        #
        # The same approach is used for service_remove_key_confirm (line 683)
        # vs service_remove_key (line 684).
        self.bot.on(events.ButtonCallback, filters.Data(b"admin_services"))(self.admin_services)
        self.bot.on(events.ButtonCallback, filters.Data(b"service_create_start"))(self.service_create_start)
        self.bot.on(events.ButtonCallback, data_regex(r"service_edit:"))(self.service_edit)
        self.bot.on(events.ButtonCallback, data_regex(r"service_delete_confirm:"))(self.service_delete_confirm)
        self.bot.on(events.ButtonCallback, data_regex(r"^service_delete:(?!confirm)"))(self.service_delete)
        self.bot.on(events.ButtonCallback, data_regex(r"service_test:"))(self.service_test)
        self.bot.on(events.ButtonCallback, data_regex(r"service_set_url:"))(self.service_set_url_start)
        self.bot.on(events.ButtonCallback, data_regex(r"service_add_key:"))(self.service_add_key_start)
        self.bot.on(events.ButtonCallback, data_regex(r"service_set_model:"))(self.service_set_model_start)
        self.bot.on(events.ButtonCallback, data_regex(r"service_remove_key_confirm:"))(self.service_remove_key_confirm)
        # ^service_remove_key: — but NOT service_remove_key_confirm:
        self.bot.on(events.ButtonCallback, data_regex(r"^service_remove_key:(?!confirm)"))(self.service_remove_key_menu)
        self.bot.on(events.ButtonCallback, data_regex(r"activate_service:"))(self.activate_service)
        self.bot.on(events.ButtonCallback, filters.Data(b"deactivate_service"))(self.deactivate_service)

        # Channel Watcher classifier model
        self.bot.on(events.ButtonCallback, filters.Data(b"cw_classifier_model"))(self.cw_classifier_model_start)

        # Second verify handlers
        self.bot.on(events.ButtonCallback, data_regex(r"verify_qa_continue:"))(self.verify_qa_continue_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_qa_switch:"))(self.verify_qa_switch_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_qa_new:"))(self.verify_qa_new_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_qa_delete_req:"))(self.verify_qa_delete_req_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_qa_delete_confirm:"))(self.verify_qa_delete_confirm_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_qa_cancel_delete:"))(self.verify_qa_cancel_delete_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_mentor_continue:"))(self.verify_mentor_continue_cb)
        self.bot.on(events.ButtonCallback, data_regex(r"verify_mentor_reset:"))(self.verify_mentor_reset_cb)

        self.bot.on(events.InlineQuery)(self.inline_query)

    async def _daily_reset_loop(self) -> None:
        """Periodic loop that resets usage metrics every 12 hours.

        Runs at 11:00 and 23:00 Tehran time. Calculates seconds until
        the next reset, sleeps, then resets all per-window and persistent
        user usage counters. Also invalidates the aggregated status cache.
        """
        while True:
            try:
                now = datetime.now(tz_tehran)
                c1 = now.replace(hour=11, minute=0, second=0, microsecond=0)
                c2 = now.replace(hour=23, minute=0, second=0, microsecond=0)
                c3 = (now + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)

                next_reset = None
                for c in [c1, c2, c3]:
                    # Use >= so that a restart at exactly 11:00:00.000000
                    # still picks the 11:00 slot instead of skipping to 23:00.
                    if c >= now:
                        next_reset = c
                        break

                if next_reset is None:
                    tlogger.warning("Could not determine next reset time. Defaulting to 12 hours.")
                    seconds_to_wait = float(12 * 3600)
                else:
                    seconds_to_wait = (next_reset - now).total_seconds()

                tlogger.info(f"Scheduled next database reset in {seconds_to_wait:.1f} seconds")
                await asyncio.sleep(seconds_to_wait)

                self.db.reset_usage_metrics()
                for svc in self.sessions.values():
                    try:
                        svc.reset_usage_metrics_only()
                    except Exception:
                        continue
                self._agg_cache = None
                self.db.save_setting("last_reset_timestamp", next_reset.isoformat())
                tlogger.info("12-hour session metrics reset completed successfully.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                tlogger.error(f"Error in reset loop: {e}")
                await asyncio.sleep(60)

    async def _cleanup_stale_data_loop(self) -> None:
        """Periodic cleanup of stale collapsed_messages and pending_message entries.

        Also prunes ``pending_message`` entries whose ``msg.date`` is
        older than 24 hours, preventing unbounded growth from abandoned
        flows (Bug #25).  ``pending_message`` has no automatic eviction
        in normal handler paths, so this loop acts as a safety net.
        """
        while True:
            try:
                await asyncio.sleep(3600)  # Every hour
                now = datetime.now()

                # ── Cleanup collapsed messages ──
                stale_collapsed = []
                for msg_id, info in self.collapsed_messages.items():
                    if info.get("auto_collapse_disabled"):
                        continue
                    task = info.get("collapse_task")
                    if task and task.done():
                        stale_collapsed.append(msg_id)
                for msg_id in stale_collapsed:
                    self.collapsed_messages.pop(msg_id, None)
                if stale_collapsed:
                    tlogger.info(f"Cleaned up {len(stale_collapsed)} stale collapsed messages")

                # ── Cleanup pending_message entries older than 24h ──
                stale_pending = []
                for key, msg in self.pending_message.items():
                    try:
                        if hasattr(msg, 'date') and msg.date and (now - msg.date).total_seconds() > 86400:
                            stale_pending.append(key)
                    except Exception:
                        pass
                for key in stale_pending:
                    self.pending_message.pop(key, None)
                if stale_pending:
                    tlogger.info(f"Cleaned up {len(stale_pending)} stale pending_message entries")
            except asyncio.CancelledError:
                break
            except Exception as e:
                tlogger.error(f"Error in cleanup loop: {e}")
                await asyncio.sleep(300)

    async def run(self) -> None:
        """Start the bot and run until disconnected.

        Performs startup tasks:
        1. Checks if a scheduled reset was missed (e.g., bot was down)
           and resets metrics if needed.
        2. Starts the Telethon client with the bot token.
        3. Loads the Trade Journal module if available.
        4. Starts the periodic reset loop as a background task.
        5. Runs until the bot is disconnected.
        """
        print("BOT IS ONLINE")

        try:
            last_reset_needed = get_last_reset_time()
            last_reset_done_str = self.db.get_setting("last_reset_timestamp", "")
            should_reset = True
            if last_reset_done_str:
                try:
                    last_reset_done = datetime.fromisoformat(last_reset_done_str)
                    if last_reset_done.tzinfo is None:
                        last_reset_done = last_reset_done.replace(tzinfo=tz_tehran)
                    if last_reset_done >= last_reset_needed:
                        should_reset = False
                except Exception as ex:
                    tlogger.error(f"Error parsing last_reset_timestamp: {ex}")

            if should_reset:
                tlogger.info("Missed reset detected on startup! Resetting metrics now...")
                self.db.reset_usage_metrics()
                for svc in self.sessions.values():
                    try:
                        svc.reset_usage_metrics_only()
                    except Exception:
                        continue
                self._agg_cache = None
                self.db.save_setting("last_reset_timestamp", last_reset_needed.isoformat())
        except Exception as startup_err:
            tlogger.error(f"Error checking missed reset on startup: {startup_err}")

        # v2: `client.start(bot_token=...)` no longer exists. v2 splits startup
        # into an explicit connect + login. For a bot account we
        # `connect()` and then `bot_sign_in(token)`. We first check
        # `is_authorized()` so an already-authenticated session skips the
        # (network) re-login. NOTE: v1 session files are NOT compatible with v2,
        # so on the first v2 run the bot will re-authenticate with its token and
        # rewrite `bot.session` in the v2 format (documented in README).
        # (see migration guide: "Changes to start and client context-manager")
        await self.bot.connect()
        if not await self.bot.is_authorized():
            await self.bot.bot_sign_in(self.bot_token)

        # Set bot client in 503 manager for notifications
        manager_503 = get_global_503_manager()
        if manager_503:
            manager_503.set_bot_client(self.bot)
            tlogger.info("503 manager bot client configured successfully")

        try:
            from trade_journal import register_handlers as register_trade_journal
            await register_trade_journal(self.bot)
            tlogger.info("Trade Journal module loaded successfully")
        except Exception as e:
            tlogger.error(f"Failed to load Trade Journal module: {e}")

        try:
            from channel_watcher import register_handlers as register_channel_watcher
            await register_channel_watcher(self.bot, self)
            tlogger.info("Channel Watcher module loaded successfully")
        except ImportError as e:
            tlogger.error(f"Failed to import Channel Watcher module: {e}")
        except Exception as e:
            tlogger.exception("Failed to load Channel Watcher module")

        # v2: `events.CallbackQuery(data=b"...")` -> `events.ButtonCallback`
        # event type + `filters.Data(b"...")` exact-match filter. The `.on()`
        # decorator still exists and takes (event_type, filter).
        @self.bot.on(events.ButtonCallback, filters.Data(b"close_window_panel"))
        async def close_window_panel(event):
            await event.answer("❌ بسته شد", alert=False)
            try:
                await event.delete()
            except Exception:
                pass

        # v2: there is no `client.loop`. Background tasks are scheduled with
        # `asyncio.create_task(...)` from within the running event loop (we are
        # already inside `asyncio.run(bot.run())`). This preserves the two
        # background loops exactly.
        # (see migration guide: "Removed client methods and properties")
        asyncio.create_task(self._daily_reset_loop())
        asyncio.create_task(self._cleanup_stale_data_loop())
        await self.bot.run_until_disconnected()
