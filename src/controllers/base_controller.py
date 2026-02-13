from typing import Any

from fastapi.responses import JSONResponse

from src.core.responses import error_response, success_response


class BaseController:
    def success(
        self, data: Any, message: str = "Request was successful", status_code: int = 200
    ) -> JSONResponse:
        return success_response(data, message, status_code)

    def failure(
        self, error: str, message: str = "An error occurred", status_code: int = 400
    ) -> JSONResponse:
        return error_response(error, message, status_code)

    def handle_error(self, exception: Exception) -> JSONResponse:
        return error_response(str(exception), status_code=500)
