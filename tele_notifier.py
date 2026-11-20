"""
Proxy module exposing TeleNotifier and install_global_error_handlers.
"""
from core.tele_notifier import (
    TeleNotifier,
    UniversalTelegramLogHandler,
    install_global_error_handlers
)

__all__ = [
    "TeleNotifier",
    "UniversalTelegramLogHandler",
    "install_global_error_handlers"
]
