from src.llms.llm_interface import LLMInterface


class DeepSeek(LLMInterface):
    def generate_code_review(self, diff: str, context: dict = None) -> str:
        return "DeepSeek code review..."
