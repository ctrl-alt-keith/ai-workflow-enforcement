from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from enforcement.org_pr_issue_scan import GhCommand, main, render_json_report, scan_org_work


class OrgPrIssueScanTests(unittest.TestCase):
    def test_scans_multiple_repositories_with_open_prs_and_issues(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("alpha")],
                    [_repo("beta")],
                ],
                "/repos/ctrl-alt-keith/alpha/pulls?state=open&per_page=100": [
                    [_item(7, "Alpha PR", repo="alpha", labels=("workflow",), assignees=("keith",))]
                ],
                "/repos/ctrl-alt-keith/alpha/issues?state=open&per_page=100": [
                    [_item(11, "Alpha issue", repo="alpha", kind="issues", author="octocat")]
                ],
                "/repos/ctrl-alt-keith/beta/pulls?state=open&per_page=100": [
                    [_item(3, "Beta PR", repo="beta")]
                ],
                "/repos/ctrl-alt-keith/beta/issues?state=open&per_page=100": [
                    [_item(4, "Beta issue", repo="beta", kind="issues", labels=("bug", "docs"))]
                ],
            }
        )

        report = scan_org_work(runner=gh)

        self.assertEqual(("alpha", "beta"), tuple(repo.name for repo in report.repositories))
        self.assertEqual(2, sum(len(repo.pull_requests) for repo in report.repositories))
        self.assertEqual(2, sum(len(repo.issues) for repo in report.repositories))
        self.assertEqual(("workflow",), report.repositories[0].pull_requests[0].labels)
        self.assertEqual(("keith",), report.repositories[0].pull_requests[0].assignees)
        self.assertEqual("octocat", report.repositories[0].issues[0].author)
        self.assertEqual("matched", report.repository_enumeration.count_attestation_status)
        self.assertEqual(2, report.repository_enumeration.enumerated_total_repositories)

    def test_issue_results_exclude_pull_requests(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [
                    [
                        _item(1, "Real issue", kind="issues"),
                        {
                            **_item(2, "PR surfaced by issues endpoint"),
                            "pull_request": {"url": "https://api.github.com/pr/2"},
                        },
                    ]
                ],
            }
        )

        report = scan_org_work(runner=gh)

        self.assertEqual([1], [issue.number for issue in report.repositories[0].issues])

    def test_paginated_repository_pr_and_issue_responses_are_flattened(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("first")],
                    [_repo("second")],
                ],
                "/repos/ctrl-alt-keith/first/pulls?state=open&per_page=100": [
                    [_item(1, "First PR page one", repo="first")],
                    [_item(2, "First PR page two", repo="first")],
                ],
                "/repos/ctrl-alt-keith/first/issues?state=open&per_page=100": [
                    [_item(3, "First issue page one", repo="first", kind="issues")],
                    [_item(4, "First issue page two", repo="first", kind="issues")],
                ],
                "/repos/ctrl-alt-keith/second/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/second/issues?state=open&per_page=100": [[]],
            }
        )

        report = scan_org_work(runner=gh)

        first = report.repositories[0]
        self.assertEqual([1, 2], [pr.number for pr in first.pull_requests])
        self.assertEqual([3, 4], [issue.number for issue in first.issues])
        for command in gh.commands:
            if "--include" in command or command[-1].startswith("/user/memberships/orgs/"):
                continue
            self.assertIn("--paginate", command)
            self.assertIn("--slurp", command)

    def test_selected_repositories_are_filtered_after_org_enumeration(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("alpha")],
                    [_repo("beta")],
                    [_repo("gamma")],
                ],
                "/repos/ctrl-alt-keith/alpha/pulls?state=open&per_page=100": [
                    [_item(1, "Alpha PR", repo="alpha")]
                ],
                "/repos/ctrl-alt-keith/alpha/issues?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/beta/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/beta/issues?state=open&per_page=100": [
                    [_item(2, "Beta issue", repo="beta", kind="issues")]
                ],
            }
        )

        report = scan_org_work(
            selected_repos=("ctrl-alt-keith/beta", "alpha", "ctrl-alt-keith/beta"),
            runner=gh,
        )
        data = json.loads(render_json_report(report))

        self.assertEqual(("beta", "alpha"), report.selected_repositories)
        self.assertEqual(("alpha", "beta"), tuple(repo.name for repo in report.repositories))
        self.assertNotIn("/repos/ctrl-alt-keith/gamma/pulls?state=open&per_page=100", [command[-1] for command in gh.commands])
        self.assertEqual(["beta", "alpha"], data["selected_repositories"])
        self.assertEqual(2, data["summary"]["repository_count"])

    def test_incomplete_enumeration_never_broadens_a_selected_report(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("alpha"), _repo("beta")],
                ],
                "/user/memberships/orgs/ctrl-alt-keith": {
                    "state": "active",
                    "role": "member",
                    "user": {"login": "operator"},
                },
                "/repos/ctrl-alt-keith/alpha/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/alpha/issues?state=open&per_page=100": [[]],
            }
        )

        report = scan_org_work(selected_repos=("alpha",), runner=gh)

        self.assertEqual(("alpha",), tuple(repo.name for repo in report.repositories))
        self.assertTrue(report.errors)
        self.assertNotIn(
            "/repos/ctrl-alt-keith/beta/pulls?state=open&per_page=100",
            [command[-1] for command in gh.commands],
        )

    def test_unknown_selected_repository_is_an_operator_error(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("alpha")],
                ],
            }
        )

        with self.assertRaises(ValueError):
            scan_org_work(selected_repos=("missing",), runner=gh)

    def test_fail_on_error_is_opt_in_for_incomplete_repository_coverage(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("blocked")]],
                "/repos/ctrl-alt-keith/blocked/pulls?state=open&per_page=100": GhCommand(
                    argv=(),
                    returncode=1,
                    stdout="",
                    stderr="HTTP 403: Forbidden",
                ),
                "/repos/ctrl-alt-keith/blocked/issues?state=open&per_page=100": [[]],
            }
        )
        report = scan_org_work(runner=gh)

        advisory_code, _ = _run_main_with_report(report)
        failing_code, _ = _run_main_with_report(report, "--fail-on-error")

        self.assertEqual(0, advisory_code)
        self.assertEqual(1, failing_code)

    def test_malformed_collection_never_reports_complete_empty_results(self) -> None:
        endpoint = "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100"
        for malformed in (
            "",
            "[]",
            "{}",
            '[{"number": 1}]',
            '[[{"number": 1}], null]',
            '[[{"number": 1}, null]]',
            "[[{}]]",
            '[[{"number": "abc", "title": "PR", "html_url": "https://github.com/example"}]]',
        ):
            with self.subTest(malformed=malformed):
                gh = FakeGh(
                    {
                        "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                        endpoint: GhCommand(argv=(), returncode=0, stdout=malformed, stderr=""),
                        "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [[]],
                    }
                )

                report = scan_org_work(runner=gh)

                self.assertEqual([], report.repositories[0].pull_requests)
                self.assertEqual(1, len(report.repositories[0].skipped))
                self.assertIn("pull requests inaccessible:", report.repositories[0].skipped[0])
                self.assertEqual(1, _run_main_with_report(report, "--fail-on-error")[0])

    def test_malformed_issue_collection_preserves_prs_and_reports_partial_coverage(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [[_item(7, "Valid PR")]],
                "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": GhCommand(
                    argv=(), returncode=0, stdout="[[null]]", stderr=""
                ),
            }
        )

        report = scan_org_work(runner=gh)

        self.assertEqual([7], [pr.number for pr in report.repositories[0].pull_requests])
        self.assertEqual([], report.repositories[0].issues)
        self.assertEqual(1, len(report.repositories[0].skipped))
        self.assertIn("issues inaccessible:", report.repositories[0].skipped[0])
        self.assertEqual(1, _run_main_with_report(report, "--fail-on-error")[0])

    def test_duplicate_page_items_mark_collection_partial_instead_of_inflating_counts(self) -> None:
        duplicate = _item(7, "Repeated PR")
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [
                    [duplicate], [duplicate]
                ],
                "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [
                    [_item(11, "Issue", kind="issues")]
                ],
            }
        )

        report = scan_org_work(runner=gh)

        self.assertEqual([], report.repositories[0].pull_requests)
        self.assertEqual([11], [issue.number for issue in report.repositories[0].issues])
        self.assertIn("duplicate work item numbers", report.repositories[0].skipped[0])
        self.assertEqual(1, _run_main_with_report(report, "--fail-on-error")[0])

    def test_work_item_from_another_repository_marks_collection_partial(self) -> None:
        for url in ("https://github.com/ctrl-alt-keith/other/pull/7", "https://[bad"):
            with self.subTest(url=url):
                gh = FakeGh(
                    {
                        "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                        "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [
                            [{**_item(7, "Foreign PR"), "html_url": url}]
                        ],
                        "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [
                            [_item(11, "Valid issue", kind="issues")]
                        ],
                    }
                )

                report = scan_org_work(runner=gh)

                self.assertEqual([], report.repositories[0].pull_requests)
                self.assertEqual([11], [issue.number for issue in report.repositories[0].issues])
                self.assertIn("mismatched URL identity", report.repositories[0].skipped[0])
                self.assertEqual(1, _run_main_with_report(report, "--fail-on-error")[0])

    def test_mismatched_or_malformed_item_urls_mark_only_their_collection_partial(self) -> None:
        invalid_urls = (
            "https://github.com/ctrl-alt-keith/sample/pull/8",  # number mismatch
            "https://github.com/ctrl-alt-keith/sample/issues/7",  # wrong item type
            "https://github.com/ctrl-alt-keith/sample/pull/7/extra",
            "https://github.com/ctrl-alt-keith/sample/pull/7/",
            "https://github.com/ctrl-alt-keith/sample/pull/7?tab=files",
            "https://github.com/ctrl-alt-keith/sample/pull/7#discussion",
            "https://github.com:443/ctrl-alt-keith/sample/pull/7",
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                gh = FakeGh(
                    {
                        "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                        "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [
                            [{**_item(7, "PR"), "html_url": url}]
                        ],
                        "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [
                            [_item(11, "Valid issue", kind="issues")]
                        ],
                    }
                )
                report = scan_org_work(runner=gh)
                self.assertEqual([], report.repositories[0].pull_requests)
                self.assertEqual([11], [issue.number for issue in report.repositories[0].issues])
                self.assertEqual(1, len(report.repositories[0].skipped))
                self.assertEqual(1, _run_main_with_report(report, "--fail-on-error")[0])

    def test_issue_with_pull_url_marks_issue_collection_partial(self) -> None:
        invalid_items = (
            _item(11, "Wrong type"),
            {**_item(11, "Wrong number", kind="issues"), "html_url": "https://github.com/ctrl-alt-keith/sample/issues/12"},
            {**_item(11, "Mislabeled PR", kind="issues"), "pull_request": {"url": "https://api.github.com/pr/11"}},
        )
        for item in invalid_items:
            with self.subTest(item=item):
                gh = FakeGh(
                    {
                        "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                        "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [[_item(7, "Valid PR")]],
                        "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [[item]],
                    }
                )
                report = scan_org_work(runner=gh)
                self.assertEqual([7], [pr.number for pr in report.repositories[0].pull_requests])
                self.assertEqual([], report.repositories[0].issues)
                self.assertEqual(1, len(report.repositories[0].skipped))
                self.assertEqual(1, _run_main_with_report(report, "--fail-on-error")[0])


class FakeGh:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> GhCommand:
        self.commands.append(argv)
        endpoint = argv[-1]
        response = self.responses.get(endpoint)
        if response is None and endpoint == "/user":
            return _included_gh(argv, {"login": "operator"})
        if response is None and endpoint == "/orgs/ctrl-alt-keith":
            pages = self.responses.get("/orgs/ctrl-alt-keith/repos?type=all&per_page=100", [])
            repositories = [item for page in pages for item in page] if isinstance(pages, list) else []
            return _included_gh(
                argv,
                {
                    "login": "ctrl-alt-keith",
                    "public_repos": sum(item.get("visibility") == "public" for item in repositories),
                    "total_private_repos": sum(item.get("visibility") == "private" for item in repositories),
                },
            )
        if response is None and endpoint.startswith("/user/memberships/orgs/"):
            response = {"state": "active", "role": "admin", "user": {"login": "operator"}}
        if response is None:
            raise KeyError(endpoint)
        if isinstance(response, GhCommand):
            return response
        return GhCommand(argv=argv, returncode=0, stdout=json.dumps(response), stderr="")


def _included_gh(argv: tuple[str, ...], body: object) -> GhCommand:
    headers = "HTTP/2 200 OK\nX-OAuth-Scopes: repo, read:org, gist\n\n"
    return GhCommand(argv=argv, returncode=0, stdout=headers + json.dumps(body), stderr="")


def _run_main_with_report(report: object, *args: str) -> tuple[int, str]:
    stdout = StringIO()
    with patch("enforcement.org_pr_issue_scan.scan_org_work", return_value=report):
        with redirect_stdout(stdout):
            code = main(list(args))
    return code, stdout.getvalue()


def _repo(name: str) -> dict[str, object]:
    return {
        "id": sum((index + 1) * ord(char) for index, char in enumerate(name)),
        "name": name,
        "full_name": f"ctrl-alt-keith/{name}",
        "owner": {"login": "ctrl-alt-keith"},
        "archived": False,
        "private": False,
        "visibility": "public",
        "default_branch": "main",
        "html_url": f"https://github.com/ctrl-alt-keith/{name}",
    }


def _item(
    number: int,
    title: str,
    *,
    repo: str = "sample",
    kind: str = "pull",
    author: str = "keith",
    labels: tuple[str, ...] = (),
    assignees: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/ctrl-alt-keith/{repo}/{kind}/{number}",
        "user": {"login": author},
        "labels": [{"name": label} for label in labels],
        "assignees": [{"login": assignee} for assignee in assignees],
        "updated_at": "2026-05-08T12:00:00Z",
    }


if __name__ == "__main__":
    unittest.main()
