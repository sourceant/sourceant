from fastapi.responses import JSONResponse
from typing import Any


class BaseController:
    def success(
        self, data: Any, message: str = "Request was successful", status_code: int = 200
    ) -> JSONResponse:
        """Return a success response with status code and JSON data"""
        response = {"status": "success", "message": message, "data": data}
        return JSONResponse(content=response, status_code=status_code)

    def failure(
        self, error: str, message: str = "An error occurred", status_code: int = 400
    ) -> JSONResponse:
        """Return a failure response with status code and error message"""
        response = {"status": "error", "message": message, "error": error}
        return JSONResponse(content=response, status_code=status_code)

    def handle_error(self, exception: Exception) -> JSONResponse:
        """Handle errors and return a standardized failure response"""
        # Log exception details if needed (can add logging)
        return self.failure(str(exception), status_code=500)
