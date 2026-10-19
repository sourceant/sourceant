import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from src.integrations.github.github import GitHub

CAPTURED = (
    Path(__file__).parents[1] / "fixtures/review-contracts/node-postgres-tree.json"
)


def github():
    with patch.dict(
        os.environ,
        {
            "GITHUB_APP_ID": "123",
            "GITHUB_APP_PRIVATE_KEY_PATH": "/path/to/key",
            "GITHUB_APP_CLIENT_ID": "456",
        },
    ):
        return GitHub()


def answered(status, payload):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode("utf-8")
    return response


@pytest.mark.parametrize("truncated", [False, True])
def test_captured_tree_lists_every_file_and_no_directory(truncated):
    payload = json.loads(CAPTURED.read_text())
    payload["truncated"] = truncated
    client = github()
    with (
        patch.object(client, "get_installation_access_token", return_value="token"),
        patch(
            "src.integrations.github.github.requests.get",
            return_value=answered(200, payload),
        ) as get,
    ):
        paths = client.list_paths("brianc", "node-postgres", "revision")
    assert get.call_args.kwargs["params"] == {"recursive": "1"}
    assert get.call_args.args[0].endswith(
        "/repos/brianc/node-postgres/git/trees/revision"
    )
    blobs = [one["path"] for one in payload["tree"] if one["type"] == "blob"]
    assert paths == blobs
    assert "packages/pg/lib/client.js" in paths
    assert "packages/pg/lib" not in paths


def test_unlistable_repository_is_an_empty_list():
    client = github()
    with (
        patch.object(client, "get_installation_access_token", return_value="token"),
        patch(
            "src.integrations.github.github.requests.get",
            return_value=answered(404, {"message": "Not Found"}),
        ),
    ):
        assert client.list_paths("owner", "missing", "revision") == []
