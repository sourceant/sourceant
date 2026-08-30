"""Who actually does the reading when an agent asks for a review.

Not the thing that was asked. An MCP server is frequently a stdio process that
lives exactly as long as the client holding it, and a review takes longer than
that: work started there dies with the process and leaves a review that says
"running" for ever. So it is handed to the agent, which is always up.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.plugins.builtin.code_reviewer.tools import _asked_of_the_agent


class Agent(BaseHTTPRequestHandler):
    asked: dict = {}
    answer: dict = {"id": "abc123", "repository": "acme/billing", "status": "running"}
    refuse: int = 0

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        Agent.asked = {
            "path": self.path,
            "body": json.loads(self.rfile.read(length) or b"{}"),
        }
        if Agent.refuse:
            self.send_response(Agent.refuse)
            self.end_headers()
            self.wfile.write(b'{"error":"name a repository"}')
            return
        body = json.dumps(Agent.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture
def agent():
    Agent.asked, Agent.refuse = {}, 0
    server = HTTPServer(("127.0.0.1", 0), Agent)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


class TestHandingTheWorkOver:
    def test_the_agent_is_asked_rather_than_this_process_doing_it(self, agent):
        _asked_of_the_agent(agent, "acme/billing", "Retry a charge")

        assert Agent.asked["path"] == "/api/reviews"
        assert Agent.asked["body"]["repository"] == "acme/billing"
        assert Agent.asked["body"]["title"] == "Retry a charge"
        # Asked for properly, or it would come back read but not judged.
        assert Agent.asked["body"]["use_model"] is True

    def test_what_comes_back_is_somewhere_to_send_a_person(self, agent):
        started = _asked_of_the_agent(agent, "acme/billing", "")

        assert started["id"] == "abc123"
        assert started["url"] == f"{agent}/reviews/abc123"

    def test_an_agent_that_is_not_there_is_said_out_loud(self):
        # Better than doing the work in a process that is about to exit.
        with pytest.raises(ValueError, match="not answering"):
            _asked_of_the_agent("http://127.0.0.1:1", "acme/billing", "")

    def test_a_refusal_is_passed_on_rather_than_swallowed(self, agent):
        Agent.refuse = 404

        with pytest.raises(ValueError, match="name a repository"):
            _asked_of_the_agent(agent, "nobody/covers-this", "")
