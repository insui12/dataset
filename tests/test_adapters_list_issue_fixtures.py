import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gbtd_infra.adapters.github import GitHubIssuesAdapter
from gbtd_infra.adapters.gitlab import GitLabIssuesAdapter
from gbtd_infra.config import AppConfig


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"x-rate-limit-remaining": "99"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeHTTP:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get(self, url, headers=None, params=None, json=None):
        self.calls.append((url, headers, params))
        return _FakeResponse(200, self.payload)


def _fixture_payload(name: str):
    path = Path("tests/fixtures") / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _new_entry(api_key: str, api_base_url: str):
    return SimpleNamespace(
        instance=SimpleNamespace(api_base_url=api_base_url),
        tracker_api_key=api_key,
    )


@pytest.mark.parametrize(
    "family_slug,payload_file,adapter_cls",
    [
        ("github", "github_issues_page.json", GitHubIssuesAdapter),
        ("gitlab", "gitlab_issues_page.json", GitLabIssuesAdapter),
    ],
)
@pytest.mark.asyncio
async def test_adapter_list_issues_from_fixture(family_slug, payload_file, adapter_cls):
    payload = _fixture_payload(payload_file)
    fake_http = _FakeHTTP(payload)

    adapter = adapter_cls(session_factory=None, client=fake_http, config=AppConfig())

    if family_slug == "github":
        entry = _new_entry(api_key="owner/repo", api_base_url="https://api.github.com")
    else:
        entry = _new_entry(api_key="group/repo", api_base_url="https://gitlab.com/api/v4")

    page = await adapter.list_issues(entry, page_size=2, mode="closed", sample_limit=None)

    assert page.error is None
    assert len(page.issues) == 2
    assert page.request_params["state"] == "closed"
    assert page.request_url is not None
    assert fake_http.calls
    assert all(item.tracker_issue_id for item in page.issues)

    if family_slug == "github":
        assert page.issues[0].issue_type_raw == "issue"
        assert page.issues[0].issue_url.startswith("https://github.com/")
    else:
        assert page.issues[0].issue_type_raw == "issue"
        assert page.issues[0].issue_url.startswith("https://gitlab.com/")
