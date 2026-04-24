"""
cs-handoff — 顧客移交提醒系統

支援兩種執行後端：
  - Celery/Redis（生產環境）
  - threading.Timer（開發/測試向後兼容）

環境變數：
  HANDOFF_USE_CELERY=true|false  (default: false)
  CELERY_BROKER_URL=redis://localhost:6379/0
  CELERY_RESULT_BACKEND=redis://localhost:6379/1
  REMIND_ENABLED=true|false
"""
import os
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, Callable, Union

logger = logging.getLogger("lovefu.handoff.reminder")

# ============================================================================
# Configuration
# ============================================================================

CELERY_BROKER = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
USE_CELERY = os.getenv("HANDOFF_USE_CELERY", "false").lower() == "true"
REMIND_ENABLED = os.getenv("REMIND_ENABLED", "true").lower() == "true"

# 提醒時間表：(階段名稱, 延遲秒數)
STAGES = [
    ("初次提醒", 30),          # 30 seconds
    ("二次提醒", 120),         # 2 minutes
    ("三次提醒", 300),         # 5 minutes
    ("升級提醒", 600),         # 10 minutes
]

# 顧客端訊息
PATIENT_MESSAGES = {
    "初次提醒": "您的服務請求已送出，我們的團隊正在為您準備。感謝您的耐心。",
    "二次提醒": "我們正在努力協調，預計短時間內會為您服務。",
    "三次提醒": "非常感謝您的等候，我們即將連接到可用的顧問。",
    "升級提醒": "如果您在此時間之後沒有收到回應，請告訴我們，我們會立即升級您的請求。",
}

# ============================================================================
# Celery App Initialization
# ============================================================================

celery_app = None
if USE_CELERY:
    try:
        from celery import Celery
        celery_app = Celery(
            "lovefu_handoff",
            broker=CELERY_BROKER,
            backend=CELERY_BACKEND,
        )
        celery_app.conf.update(
            task_serializer="json",
            accept_content=["json"],
            result_serializer="json",
            timezone="Asia/Taipei",
            task_track_started=True,
        )
        logger.info("Celery app initialized successfully")
    except ImportError:
        logger.warning("Celery not installed, falling back to threading.Timer")
        USE_CELERY = False


# ============================================================================
# Celery Tasks (if enabled)
# ============================================================================

if USE_CELERY and celery_app:

    @celery_app.task(name="lovefu.handoff.fire_stage")
    def _celery_fire_stage(stage_name: str, handoff_id: str) -> dict:
        """
        Celery task wrapper for stage callback.
        Called asynchronously when reminder delay expires.
        """
        logger.info(f"Celery task fired: stage={stage_name}, handoff_id={handoff_id}")
        message = patient_message_for_elapsed(stage_name)
        return {
            "stage": stage_name,
            "handoff_id": handoff_id,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
        }


# ============================================================================
# In-Memory Storage (shared between Celery and threading modes)
# ============================================================================

_scheduled: dict[str, Union[list[str], list[threading.Timer]]] = {}


# ============================================================================
# Helper Functions
# ============================================================================

def patient_message_for_elapsed(stage_name: str) -> str:
    """
    Return the patient message for a given stage.
    Keeps original behavior for all STAGES.
    """
    return PATIENT_MESSAGES.get(stage_name, "請稍候。")


def _fire_stage(stage_name: str, handoff_id: str, on_stage: Optional[Callable]) -> None:
    """
    Internal callback for threading.Timer mode.
    Fires when delay expires.
    """
    logger.info(f"Stage fired (threading): stage={stage_name}, handoff_id={handoff_id}")
    if on_stage:
        message = patient_message_for_elapsed(stage_name)
        try:
            on_stage(
                stage_name=stage_name,
                handoff_id=handoff_id,
                message=message,
            )
        except Exception as e:
            logger.error(f"Error in on_stage callback: {e}", exc_info=True)


# ============================================================================
# Public API
# ============================================================================

def schedule_reminders(
    handoff_id: str,
    on_stage: Optional[Callable] = None,
) -> None:
    """
    Schedule stage reminders for a handoff.

    Args:
        handoff_id: Unique handoff identifier
        on_stage: Callback function called when each stage fires.
                  Signature: on_stage(stage_name, handoff_id, message)

    If USE_CELERY=true and celery_app is available, uses Celery async tasks.
    Otherwise, falls back to threading.Timer.
    """
    if not REMIND_ENABLED:
        logger.info(f"Reminders disabled (REMIND_ENABLED=false) for {handoff_id}")
        return

    if USE_CELERY and celery_app:
        logger.info(f"Scheduling reminders (Celery) for {handoff_id}")
        task_ids = []
        for stage_name, delay_sec in STAGES:
            result = _celery_fire_stage.apply_async(
                args=[stage_name, handoff_id],
                countdown=delay_sec,
            )
            task_ids.append(result.id)
            logger.debug(f"Celery task scheduled: {result.id} for stage {stage_name}")

        _scheduled[handoff_id] = task_ids
    else:
        logger.info(f"Scheduling reminders (threading) for {handoff_id}")
        timers = []
        for stage_name, delay_sec in STAGES:
            t = threading.Timer(
                delay_sec,
                _fire_stage,
                args=(stage_name, handoff_id, on_stage),
            )
            t.daemon = True
            t.start()
            timers.append(t)
            logger.debug(f"Timer scheduled for stage {stage_name} in {delay_sec}s")

        _scheduled[handoff_id] = timers


def cancel_reminders(handoff_id: str) -> None:
    """
    Cancel all scheduled reminders for a handoff.

    If using Celery, revokes the tasks.
    If using threading, cancels the timers.
    """
    handlers = _scheduled.get(handoff_id)
    if not handlers:
        logger.debug(f"No reminders scheduled for {handoff_id}")
        return

    if USE_CELERY and celery_app:
        logger.info(f"Revoking {len(handlers)} Celery tasks for {handoff_id}")
        for task_id in handlers:
            try:
                celery_app.control.revoke(task_id, terminate=True)
                logger.debug(f"Revoked task {task_id}")
            except Exception as e:
                logger.error(f"Failed to revoke task {task_id}: {e}")
    else:
        logger.info(f"Cancelling {len(handlers)} timers for {handoff_id}")
        for timer in handlers:
            try:
                timer.cancel()
                logger.debug(f"Cancelled timer")
            except Exception as e:
                logger.error(f"Failed to cancel timer: {e}")

    _scheduled.pop(handoff_id, None)


def get_scheduled_reminders(handoff_id: str) -> dict:
    """
    Get the status of scheduled reminders for a handoff.
    Useful for debugging.
    """
    handlers = _scheduled.get(handoff_id)
    if not handlers:
        return {"handoff_id": handoff_id, "status": "none"}

    if USE_CELERY and celery_app:
        return {
            "handoff_id": handoff_id,
            "backend": "celery",
            "task_ids": handlers,
            "count": len(handlers),
        }
    else:
        return {
            "handoff_id": handoff_id,
            "backend": "threading",
            "timer_count": len(handlers),
        }
