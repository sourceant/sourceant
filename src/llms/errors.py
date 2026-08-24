class LLMError(Exception):
    """A call to the model provider failed.

    Carries the provider's own exception rather than replacing it, because the
    caller cannot tell an expired key from an exhausted quota from a prompt that
    overran the context window, and each of those needs a different response.
    """

    def __init__(self, message: str, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause

    @classmethod
    def wrapping(cls, action: str, cause: BaseException) -> "LLMError":
        return cls(f"{action} failed: {type(cause).__name__}: {cause}", cause)
