"""Tests for IssueStream behavior."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from tap_jira.client import PageWatermarkError
from tap_jira.streams import IssueStream
from tap_jira.tap import TapJira

BASE_CONFIG = {
    "domain": "test.atlassian.net",
    "email": "test@example.com",
    "api_token": "test-token",
}


def _issue_stream(config: dict | None = None) -> IssueStream:
    return IssueStream(TapJira(config={**BASE_CONFIG, **(config or {})}))


def _response(issues: list[dict]) -> Mock:
    response = Mock()
    response.json.return_value = {"issues": issues}
    return response


def _issue(
    *,
    issue_id: str = "10001",
    created: str | None = "2026-01-01T00:00:00.000+0000",
    updated: str | None = "2026-01-02T00:00:00.000+0000",
) -> dict:
    return {
        "id": issue_id,
        "key": "JIRA-1",
        "fields": {
            "summary": "a business field",
            "created": created,
            "updated": updated,
        },
    }


def test_get_url_params_jql_start_date() -> None:
    tap = TapJira(
        config={
            "domain": "test.atlassian.net",
            "email": "test@example.com",
            "api_token": "test-token",
        },
        state={
            "bookmarks": {
                "issues": {
                    "starting_replication_value": "2026-01-01T12:34:56",
                },
            },
        },
    )

    stream = IssueStream(tap)
    params = stream.get_url_params(context=None, next_page_token=None)

    assert params["jql"] == (
        "(created>='2026-01-01 12:34' or updated>='2026-01-01 12:34') and (id != null) "
        "order by updated asc"
    )


def test_get_url_params_jql_start_and_end_date() -> None:
    tap = TapJira(
        config={
            "domain": "test.atlassian.net",
            "email": "test@example.com",
            "api_token": "test-token",
            "end_date": "2026-02-01T00:00:00",
        },
        state={
            "bookmarks": {
                "issues": {
                    "starting_replication_value": "2026-01-01T12:34:56",
                },
            },
        },
    )

    stream = IssueStream(tap)
    params = stream.get_url_params(context=None, next_page_token=None)

    assert params["jql"] == (
        "(created>='2026-01-01 12:34' or updated>='2026-01-01 12:34') and "
        "(created<'2026-02-01 00:00' or updated<'2026-02-01 00:00') and (id != null) "
        "order by updated asc"
    )


class TestFieldsParamProjection:
    """The configured fields projection is separate from API-required fields."""

    def test_default_wildcard_passthrough(self) -> None:
        """No explicit projection means the wildcard selector is untouched."""
        assert _issue_stream().get_fields_param() == "*all"

    def test_explicit_wildcard_passthrough(self) -> None:
        """Wildcard selectors keep their meaning instead of being expanded."""
        stream = _issue_stream(
            {"stream_options": {"issues": {"fields": "*navigable"}}},
        )
        assert stream.get_fields_param() == "*navigable"

    def test_required_fields_appended_to_business_projection(self) -> None:
        """A narrow business-only selection is completed with plumbing fields."""
        stream = _issue_stream(
            {"stream_options": {"issues": {"fields": "summary,status"}}},
        )
        assert stream.get_fields_param() == "summary,status,id,created,updated"

    def test_required_fields_not_duplicated(self) -> None:
        """Fields already present in the projection are not repeated."""
        stream = _issue_stream(
            {"stream_options": {"issues": {"fields": "summary,updated,id"}}},
        )
        assert stream.get_fields_param() == "summary,updated,id,created"

    def test_whitespace_tolerated(self) -> None:
        """Whitespace around field names is stripped before merging."""
        stream = _issue_stream(
            {"stream_options": {"issues": {"fields": " summary , status "}}},
        )
        assert stream.get_fields_param() == "summary,status,id,created,updated"

    def test_request_params_carry_completed_fields(self) -> None:
        """The completion logic flows through the actual request parameters."""
        stream = _issue_stream(
            {"stream_options": {"issues": {"fields": "summary"}}},
        )
        params = stream.get_url_params(context=None, next_page_token=None)
        assert params["fields"] == "summary,id,created,updated"


class TestPageWatermarkValidation:
    """Pages missing watermark fields fail as a whole."""

    def test_valid_page_yields_records(self) -> None:
        """A page carrying all required fields parses normally."""
        stream = _issue_stream()
        records = list(stream.parse_response(_response([_issue()])))
        assert records[0]["id"] == "10001"

    def test_missing_updated_fails_page(self) -> None:
        """A dropped replication key aborts the page before records are emitted."""
        stream = _issue_stream()
        response = _response([_issue(updated=None)])
        with pytest.raises(PageWatermarkError, match="updated"):
            list(stream.parse_response(response))

    def test_missing_id_fails_page(self) -> None:
        """A dropped primary key (child-stream context) aborts the page."""
        stream = _issue_stream()
        response = _response([_issue(issue_id=None)])
        with pytest.raises(PageWatermarkError, match="id"):
            list(stream.parse_response(response))

    def test_missing_created_fails_page(self) -> None:
        """The second replication key is guarded as well."""
        stream = _issue_stream()
        response = _response([_issue(created=None)])
        with pytest.raises(PageWatermarkError, match="created"):
            list(stream.parse_response(response))

    def test_missing_fields_object_fails_page(self) -> None:
        """A record without a nested fields object cannot hold the watermark."""
        stream = _issue_stream()
        response = _response([{"id": "10001", "key": "JIRA-1"}])
        with pytest.raises(PageWatermarkError):
            list(stream.parse_response(response))
