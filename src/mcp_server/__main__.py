import os

from src.core.code_index import InMemoryCodeIndex
from src.core.context import DefaultContextProvider
from src.core.contracts import InMemoryContractRepository
from src.core.knowledge import SQLiteKnowledgeRepository
from src.core.review_state import InMemoryReviewStateRepository
from src.core.topology import InMemoryTopologyRepository

from .server import create_mcp_server


def main() -> None:
    knowledge = SQLiteKnowledgeRepository(
        os.environ.get("SOURCEANT_KNOWLEDGE_DB", ".sourceant/knowledge.db")
    )
    provider = DefaultContextProvider(
        code=InMemoryCodeIndex(),
        knowledge=knowledge,
        topology=InMemoryTopologyRepository(),
        contracts=InMemoryContractRepository(),
        review_state=InMemoryReviewStateRepository(),
    )
    create_mcp_server(provider, knowledge=knowledge).run()


if __name__ == "__main__":
    main()
