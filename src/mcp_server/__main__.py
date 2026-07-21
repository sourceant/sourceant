from src.core.code_index import InMemoryCodeIndex
from src.core.context import DefaultContextProvider
from src.core.contracts import InMemoryContractRepository
from src.core.knowledge import InMemoryKnowledgeRepository
from src.core.review_state import InMemoryReviewStateRepository
from src.core.topology import InMemoryTopologyRepository

from .server import create_mcp_server


def main() -> None:
    provider = DefaultContextProvider(
        code=InMemoryCodeIndex(),
        knowledge=InMemoryKnowledgeRepository(),
        topology=InMemoryTopologyRepository(),
        contracts=InMemoryContractRepository(),
        review_state=InMemoryReviewStateRepository(),
    )
    create_mcp_server(provider).run()


if __name__ == "__main__":
    main()
