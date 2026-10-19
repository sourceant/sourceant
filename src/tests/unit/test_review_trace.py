import asyncio
from pathlib import Path

from src.core.review_trace import ReviewTraceSink, TraceKind, finding, traced_review
from src.core.services import ServiceRegistry
from src.models.code_review import CodeSuggestion


def captured_finding():
    path = (
        Path(__file__).parents[1]
        / "fixtures/review-contracts/techdebt-numeric-flags-candidate.json"
    )
    return CodeSuggestion.model_validate_json(path.read_text())


def test_finding_lineage_survives_anchor_mutation_and_distinguishes_objects():
    events = []

    class Sink:
        def record(self, event):
            events.append(event)

    class Owner:
        services = ServiceRegistry().with_service(ReviewTraceSink, Sink(), "test")

        @traced_review
        def review(self):
            suggestion = captured_finding()
            finding("discovery", "raw", suggestion)
            suggestion.start_line = suggestion.end_line
            finding("processing", "retained", suggestion)
            finding("discovery", "raw", captured_finding())

    Owner().review()
    assert events[0].finding_id == events[1].finding_id
    assert events[2].finding_id != events[0].finding_id
    assert [event.sequence for event in events] == [1, 2, 3]
    assert all(event.kind == TraceKind.FINDING for event in events)
    finding("outside", "raw", captured_finding())
    assert len(events) == 3


def test_async_trace_scope_remains_bound_until_awaited_work_finishes():
    events = []

    class Sink:
        def record(self, event):
            events.append(event)

    class Owner:
        services = ServiceRegistry().with_service(ReviewTraceSink, Sink(), "test")

        @traced_review
        async def review(self):
            await asyncio.sleep(0)
            finding("discovery", "raw", captured_finding())

    asyncio.run(Owner().review())
    assert len(events) == 1
    finding("outside", "raw", captured_finding())
    assert len(events) == 1
