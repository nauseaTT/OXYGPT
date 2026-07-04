"""Centralized manager for Gemini 503 error handling and auto-recovery.

این ماژول مسئولیتهای زیر رو داره:
1. Track کردن 503 errors در سطح instance
2. تصمیمگیری برای downgrade به 3.1-flash-lite
3. Scheduling auto-recovery task
4. ارسال notification به admin
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from database import DatabaseManager

logger = logging.getLogger("gemini_503_manager")

# Global singleton instance
_global_manager: Optional['Gemini503Manager'] = None


class Gemini503Manager:
    """مدیریت مرکزی 503 errors و auto-recovery."""
    
    def __init__(self, db_manager: DatabaseManager, admin_user_id: int):
        """
        Args:
            db_manager: Database manager instance
            admin_user_id: Telegram user ID of admin (for notifications)
        """
        self.db_manager = db_manager
        self.admin_user_id = admin_user_id
        self._503_count: int = 0  # Counter for 503 errors
        self._recovery_task: Optional[asyncio.Task] = None
        self._bot_client = None  # Will be set by TelegramBot
        
    def set_bot_client(self, bot_client) -> None:
        """Set the Telegram bot client for sending notifications.
        
        Args:
            bot_client: Telethon client instance from TelegramBot
        """
        self._bot_client = bot_client
        
    async def handle_503_error(self, model_name: str, user_id: int) -> str:
        """Handle a 503 error occurrence.
        
        این متد هر بار که 503 اتفاق میفته صدا زده میشه.
        
        Args:
            model_name: نام مدلی که 503 داده
            user_id: User ID که درخواست کرده
            
        Returns:
            Fallback model name to use (همیشه "gemini-3.1-flash-lite")
        """
        self._503_count += 1
        logger.warning(
            f"[503 MANAGER] 503 error #{self._503_count} detected. "
            f"Model: {model_name}, User: {user_id}"
        )
        
        # Check if we need to trigger database downgrade
        if self._503_count >= 2:
            await self._trigger_database_downgrade()
            
        return "gemini-3.1-flash-lite"
        
    async def _trigger_database_downgrade(self) -> None:
        """Trigger database-wide downgrade and schedule recovery."""
        # Check if already downgraded
        state = self.db_manager.get_503_state()
        if state["is_downgraded"]:
            logger.info("[503 MANAGER] Already in downgrade state, skipping.")
            return
            
        logger.warning("[503 MANAGER] CRITICAL: Triggering database-wide downgrade!")
        
        # Downgrade all gemini-3.5-flash models to 3.1-flash-lite
        changed_models = self.db_manager.downgrade_gemini_models()
        
        if not changed_models:
            logger.info("[503 MANAGER] No models needed downgrade.")
            return
            
        # Save state
        self.db_manager.set_503_downgrade(changed_models)
        
        # Send admin notification
        await self._send_admin_notification(
            "⚠️ <b>Gemini 503 Auto-Downgrade Activated</b>\n\n"
            f"🔻 Changed models:\n" + 
            "\n".join(f"  • <code>{k}</code>: {v} → gemini-3.1-flash-lite" 
                     for k, v in changed_models.items()) +
            f"\n\n⏰ Auto-recovery scheduled in 30 minutes."
        )
        
        # Schedule recovery
        await self._schedule_recovery()
        
    async def _schedule_recovery(self) -> None:
        """Schedule auto-recovery task for 30 minutes later."""
        # Cancel any existing recovery task
        if self._recovery_task and not self._recovery_task.done():
            self._recovery_task.cancel()
            
        # Create new recovery task
        self._recovery_task = asyncio.create_task(self._recovery_worker())
        logger.info("[503 MANAGER] Recovery task scheduled for 30 minutes.")
        
    async def _recovery_worker(self) -> None:
        """Background worker that waits 30 minutes and restores models."""
        try:
            # Wait 30 minutes
            await asyncio.sleep(30 * 60)
            
            logger.info("[503 MANAGER] 30 minutes passed, starting recovery...")
            
            # Restore models
            state = self.db_manager.get_503_state()
            if not state["is_downgraded"]:
                logger.warning("[503 MANAGER] Not in downgrade state, nothing to recover.")
                return
                
            original_models = state["original_models"]
            self.db_manager.restore_gemini_models(original_models)
            self.db_manager.clear_503_downgrade()
            
            # Reset counter
            self._503_count = 0
            
            # Send confirmation
            await self._send_admin_notification(
                "✅ <b>Gemini Models Auto-Recovered</b>\n\n"
                f"🔼 Restored models:\n" +
                "\n".join(f"  • <code>{k}</code>: {v}" 
                         for k, v in original_models.items()) +
                "\n\n🟢 System back to normal."
            )
            
            logger.info("[503 MANAGER] Recovery completed successfully.")
            
        except asyncio.CancelledError:
            logger.info("[503 MANAGER] Recovery task cancelled.")
            raise
        except Exception as e:
            logger.error(f"[503 MANAGER] Recovery task failed: {e}")
            await self._send_admin_notification(
                f"❌ <b>Auto-Recovery Failed</b>\n\n"
                f"Error: <code>{e}</code>\n\n"
                f"Please check manually."
            )
            
    async def _send_admin_notification(self, message: str) -> None:
        """Send a notification message to admin.
        
        Args:
            message: HTML-formatted message to send
        """
        if not self._bot_client:
            logger.error("[503 MANAGER] Bot client not set, cannot send notification.")
            return
            
        try:
            await self._bot_client.send_message(
                self.admin_user_id,
                message,
                parse_mode="html"
            )
            logger.info(f"[503 MANAGER] Notification sent to admin {self.admin_user_id}.")
        except Exception as e:
            logger.error(f"[503 MANAGER] Failed to send notification: {e}")
            
    def reset_counter(self) -> None:
        """Reset 503 error counter (called after successful request)."""
        if self._503_count > 0:
            logger.info(f"[503 MANAGER] Resetting counter from {self._503_count} to 0.")
            self._503_count = 0
            
    def get_stats(self) -> Dict[str, Any]:
        """Get current manager statistics.
        
        Returns:
            Dict with current state and stats
        """
        state = self.db_manager.get_503_state()
        return {
            "503_count": self._503_count,
            "is_downgraded": state["is_downgraded"],
            "downgrade_time": state["downgrade_time"],
            "recovery_scheduled": state["recovery_scheduled"],
            "recovery_task_active": self._recovery_task is not None and not self._recovery_task.done()
        }


# Singleton pattern functions
def init_global_503_manager(db_manager: DatabaseManager, admin_user_id: int) -> None:
    """Initialize the global 503 manager instance.
    
    Args:
        db_manager: Database manager instance
        admin_user_id: Admin user ID for notifications
    """
    global _global_manager
    if _global_manager is None:
        _global_manager = Gemini503Manager(db_manager, admin_user_id)
        logger.info(f"[503 MANAGER] Global instance initialized with admin_id={admin_user_id}")
    else:
        logger.warning("[503 MANAGER] Global instance already initialized, skipping.")


def get_global_503_manager() -> Optional[Gemini503Manager]:
    """Get the global 503 manager instance.
    
    Returns:
        The global manager instance, or None if not initialized
    """
    return _global_manager
