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


def _quiet_stdout() -> None:
    """Send everything that logs anywhere near stdout to stderr instead."""
    for logger in [logging.getLogger()] + [
        logging.getLogger(name) for name in logging.root.manager.loggerDict
    ]:
        for handler in list(getattr(logger, "handlers", [])):
            if getattr(handler, "stream", None) is sys.stdout:
                handler.setStream(sys.stderr)


def main() -> None:
    _quiet_stdout()
    from .application import create_default_mcp_server

    # Anything set up during import gets the same treatment: the engine, the
    # plugins, and whatever they brought with them.
    _quiet_stdout()
    create_default_mcp_server().run()


if __name__ == "__main__":
    main()
