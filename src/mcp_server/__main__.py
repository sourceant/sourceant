"""Running the MCP server over stdin and stdout.

Nothing but protocol may go to stdout. A client reads that stream a line at a
time and parses each line as JSON-RPC, so one log line lands in the middle of a
conversation and the message it interrupted is lost. The ordinary logging here
writes anything at INFO to stdout, which is right for a server in a terminal
and wrong for this, so it is sent to stderr before anything else happens.
"""

import logging
import os
import sys

# Set, not defaulted, and before anything reads the settings: the logger is
# built on the way in, and a `.env` beside the checkout is enough to put it
# back on stdout. Nothing about this process makes stdout available for logs.
os.environ["LOG_DRIVER"] = "stderr"
os.environ["DEBUG_MODE"] = "false"

# SQLAlchemy echoes every statement once its logger sits at INFO, which any
# root-level basicConfig does. Silenced here, before the first engine exists.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)


def _quiet_stdout() -> None:
    """Send everything that logs anywhere near stdout to stderr instead."""
    for logger in [logging.getLogger()] + [
        logging.getLogger(name) for name in logging.root.manager.loggerDict
    ]:
        for handler in list(getattr(logger, "handlers", [])):
            if getattr(handler, "stream", None) is sys.stdout:
                handler.setStream(sys.stderr)


def main() -> None:
    # Stdout does not exist until the transport owns it. Imported libraries
    # configure logging on import and capture whatever sys.stdout is at that
    # moment, so anything they set up during setup is bound to stderr for the
    # life of the process and cannot interrupt a JSON-RPC conversation later.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        _quiet_stdout()
        from .application import create_default_mcp_server, load_plugins

        load_plugins()

        server = create_default_mcp_server()
    finally:
        sys.stdout = real_stdout

    _quiet_stdout()
    server.run()


if __name__ == "__main__":
    main()
