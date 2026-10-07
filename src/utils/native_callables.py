from __future__ import annotations

import inspect
from functools import update_wrapper

from mcp.server.fastmcp import FastMCP

from .native_types import FUNCTION_TYPES


def python_callable(function):
    if inspect.isfunction(function) or not isinstance(function, FUNCTION_TYPES):
        return function
    namespace = dict(function.__globals__)
    namespace["_target"] = function
    asynchronous = bool(function.__code__.co_flags & inspect.CO_COROUTINE)
    source = (
        "async def _call(*args, **kwargs):\n    return await _target(*args, **kwargs)\n"
        if asynchronous
        else "def _call(*args, **kwargs):\n    return _target(*args, **kwargs)\n"
    )
    exec(source, namespace)
    wrapper = update_wrapper(namespace["_call"], function)
    wrapper.__signature__ = inspect.signature(function, eval_str=True)
    return wrapper


class NativeFastMCP(FastMCP):
    def add_tool(self, fn, *args, **kwargs):
        return super().add_tool(python_callable(fn), *args, **kwargs)

    def prompt(self, *args, **kwargs):
        register = super().prompt(*args, **kwargs)

        def decorated(function):
            return register(python_callable(function))

        return decorated
