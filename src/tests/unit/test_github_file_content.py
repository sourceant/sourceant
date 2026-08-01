import base64
import json
from unittest.mock import patch

import pytest
import requests

from src.integrations.github.github import GitHub


def test_file_content_reports_non_utf8_content_as_unavailable():
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps(
        {"content": base64.b64encode(b"\xff").decode("ascii")}
    ).encode("utf-8")
    github = GitHub()

    with (
        patch.object(github, "get_installation_access_token", return_value="token"),
        patch("src.integrations.github.github.requests.get", return_value=response),
        pytest.raises(ValueError, match="Failed to decode file content"),
    ):
        github.get_file_content("owner", "repository", "logo.png", "revision")
