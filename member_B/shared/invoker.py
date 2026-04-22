from __future__ import annotations

from importlib import import_module
from threading import Thread
from typing import Any


HandlerEvent = dict[str, Any]


def invoke(handler_path: str, event: HandlerEvent) -> Any:
    module_name, function_name = handler_path.split(':', 1)
    module = import_module(module_name)
    handler = getattr(module, function_name)
    return handler(event, None)


def invoke_submission_event_async(event: HandlerEvent) -> Thread:
    thread = Thread(
        target=invoke,
        args=('functions.submission_event.handler:handler', event),
        daemon=True,
    )
    thread.start()
    return thread
