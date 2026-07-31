import httpx
import pytest

from src.utils.provider_pages import fetch_all, next_page_url

# A Link header exactly as GitHub sends one.
GITHUB_LINK = (
    '<https://api.github.com/user/repos?per_page=100&page=2>; rel="next", '
    '<https://api.github.com/user/repos?per_page=100&page=10>; rel="last"'
)


class TestNextPageUrl:
    def test_takes_the_next_page_from_a_real_header(self):
        assert next_page_url(GITHUB_LINK) == (
            "https://api.github.com/user/repos?per_page=100&page=2"
        )

    def test_has_no_next_page_on_the_last_one(self):
        last = '<https://api.github.com/user/repos?page=1>; rel="prev"'
        assert next_page_url(last) is None

    def test_has_no_next_page_without_a_header(self):
        assert next_page_url(None) is None


def _client(pages, link_for):
    """A client answering a fixed set of pages, each pointing at the next."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        index = len(calls) - 1
        headers = {}
        link = link_for(index)
        if link:
            headers["Link"] = link
        return httpx.Response(200, json=pages[index], headers=headers)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport), calls


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_reads_every_page_the_provider_offers(self):
        pages = [[{"id": 1}, {"id": 2}], [{"id": 3}]]
        client, calls = _client(
            pages,
            lambda i: (
                '<https://api.github.com/user/repos?page=2>; rel="next"'
                if i == 0
                else None
            ),
        )
        async with client:
            items, truncated = await fetch_all(
                client, "https://api.github.com/user/repos", {}
            )

        assert [item["id"] for item in items] == [1, 2, 3]
        assert truncated is False
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_stops_at_the_ceiling_and_says_it_did(self):
        pages = [[{"id": i}] for i in range(5)]
        client, _ = _client(
            pages, lambda i: '<https://api.github.com/x?page=9>; rel="next"'
        )
        async with client:
            items, truncated = await fetch_all(
                client, "https://api.github.com/x", {}, max_pages=2
            )

        assert len(items) == 2
        # The caller can say the list is short rather than implying it is whole.
        assert truncated is True

    @pytest.mark.asyncio
    async def test_a_single_page_is_not_truncated(self):
        client, calls = _client([[{"id": 1}]], lambda i: None)
        async with client:
            items, truncated = await fetch_all(client, "https://api.github.com/x", {})

        assert len(items) == 1
        assert truncated is False
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_keeps_what_it_read_when_a_later_page_fails(self):
        def handler(request):
            if "page=2" in str(request.url):
                return httpx.Response(502)
            return httpx.Response(
                200,
                json=[{"id": 1}],
                headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            items, truncated = await fetch_all(client, "https://api.github.com/x", {})

        assert len(items) == 1
        assert truncated is True

    @pytest.mark.asyncio
    async def test_asks_for_the_largest_page_the_provider_allows(self):
        client, calls = _client([[{"id": 1}]], lambda i: None)
        async with client:
            await fetch_all(
                client, "https://api.github.com/x", {}, params={"sort": "updated"}
            )

        assert "per_page=100" in calls[0]
        assert "sort=updated" in calls[0]
