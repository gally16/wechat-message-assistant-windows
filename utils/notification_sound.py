# -*- coding: utf-8 -*-
"""Notification sound fallback for Windows 10/11.

winotify's toast audio can be muted by Windows notification settings/focus assist.
This module provides an optional winsound fallback so the app can still play a
short system sound when a notification is pushed.
"""
from __future__ import annotations

import logging


def play_notification_sound(enabled: bool = True, alias: str = "SystemAsterisk") -> None:
    if not enabled:
        return
    try:
        import winsound
        # Common aliases: SystemAsterisk, SystemExclamation, SystemNotification, SystemDefault
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception as e:
        logging.debug("播放通知声音失败: %s", e)
