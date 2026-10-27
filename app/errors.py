"""Errors shown to the user. The UI translates them by code (the error.<code> keys in static/i18n.js)."""
from typing import Any


class AppError(Exception):
    """
    An error with a stable code and parameters for the UI. The message is English and is only
    used in logs and as a fallback when the UI has no translation for the code.
    """

    def __init__(self, code: str, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code = code
        self.params = params

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "params": self.params, "message": str(self)}


def error_detail(exc: BaseException) -> dict[str, Any]:
    """API error detail for any exception: AppError keeps its code, anything else becomes "unknown"."""
    if isinstance(exc, AppError):
        return exc.to_detail()
    return {"code": "unknown", "params": {"message": str(exc)}, "message": str(exc)}
