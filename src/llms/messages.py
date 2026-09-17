def assistant_message(answer: dict) -> dict:
    if "assistant_message" in answer:
        return answer["assistant_message"]
    return {
        "role": "assistant",
        "content": answer.get("content") or "",
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": call["arguments"],
                },
            }
            for call in answer.get("tool_calls", ())
        ],
    }
