from src.core.review_search import WhatToLookFor
from src.core.topology.proposing import WhatItReads
from src.llms.messages import assistant_message


class Conversation:
    def __init__(self, call):
        self.call = call
        self.message = {"opaque_state": "keep unchanged"}
        self.followup = None

    def ask_with_tools(self, messages, tools, **kwargs):
        if self.followup is None and len(messages) == 1:
            return {
                "content": "",
                "tool_calls": [self.call],
                "assistant_message": self.message,
            }
        self.followup = list(messages)
        return {"content": "", "tool_calls": []}


def test_discovery_carries_the_full_assistant_message_to_the_next_round():
    model = Conversation(
        {"id": "read", "name": "read_file", "arguments": '{"path":"README.md"}'}
    )
    WhatItReads(lambda path: "Repository", lambda terms: ()).propose(
        model, entity_id="checkout", repository="acme/checkout", targets=("billing",)
    )
    assert model.followup[1] is model.message
    assert model.followup[2]["role"] == "tool"


def test_review_search_carries_the_full_assistant_message_to_the_next_round():
    from src.core.scope import Scope
    from src.core.search import SearchResult

    class Searcher:
        def search(self, query):
            return SearchResult(())

    model = Conversation(
        {
            "id": "search",
            "name": "search_code",
            "arguments": '{"repository":"acme/checkout","terms":["billing"]}',
        }
    )
    WhatToLookFor(
        Searcher(), lambda repository: Scope.from_mapping({"repository": repository})
    ).gather(model, change="billing", repositories=("acme/checkout",))
    assert model.followup[1] is model.message
    assert model.followup[2]["role"] == "tool"


def test_plugins_without_an_assistant_message_keep_the_existing_contract():
    assert assistant_message({"content": "done", "tool_calls": []}) == {
        "role": "assistant",
        "content": "done",
        "tool_calls": [],
    }
