from src.llms.llm_interface import LLMInterface


class DeepSeek(LLMInterface):
    def generate_code_review(self, diff: str, context: str = "None") -> str:
        return "DeepSeek code review..."
