class Refused(Exception):
    """Something this environment cannot do, with the status to answer with.

    Carries the status rather than raising an HTTP error, since a plugin does
    not know it is behind a web server.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
