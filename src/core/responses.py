"""Standardized API response helpers.

All API endpoints (core routes and plugins) should use these functions
to ensure a consistent response format across the application.

Success: {"status": "success", "message": "...", "data": ...}
Error:   {"status": "error", "message": "...", "error": "..."}
"""

from typing import Any

from fastapi.responses import JSONResponse


def success_response(
    data: Any,
    message: str = "Request was successful",
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        content={"status": "success", "message": message, "data": data},
        status_code=status_code,
    )


def error_response(
    error: str,
    message: str = "An error occurred",
    status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        content={"status": "error", "message": message, "error": error},
        status_code=status_code,
    )
