from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import patch

from enforcement.org_pr_issue_scan import GhCommand, main, render_json_report, render_text_report, scan_org_work


class OrgPrIssueScanTests(unittest.TestCase):
    def test_scans_multiple_repositories_with_open_prs_and_issues(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("alpha")],
                    [_repo("beta")],
                ],
                "/repos/ctrl-alt-keith/alpha/pulls?state=open&per_page=100": [
                    [_item(7, "Alpha PR", labels=("workflow",), assignees=("keith",))]
                ],
                "/repos/ctrl-alt-keith/alpha/issues?state=open&per_page=100": [
                    [_item(11, "Alpha issue", author="octocat")]
                ],
                "/repos/ctrl-alt-keith/beta/pulls?state=open&per_page=100": [
                    [_item(3, "Beta PR")]
                ],
                "/repos/ctrl-alt-keith/beta/issues?state=open&per_page=100": [
                    [_item(4, "Beta issue", labels=("bug", "docs"))]
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

    def test_issue_results_exclude_pull_requests(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("sample")]],
                "/repos/ctrl-alt-keith/sample/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/sample/issues?state=open&per_page=100": [
                    [
                        _item(1, "Real issue"),
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
                    [_item(1, "First PR page one")],
                    [_item(2, "First PR page two")],
                ],
                "/repos/ctrl-alt-keith/first/issues?state=open&per_page=100": [
                    [_item(3, "First issue page one")],
                    [_item(4, "First issue page two")],
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
            if command[-1].startswith("/user/memberships/orgs/"):
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
                    [_item(1, "Alpha PR")]
                ],
                "/repos/ctrl-alt-keith/alpha/issues?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/beta/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/beta/issues?state=open&per_page=100": [
                    [_item(2, "Beta issue")]
                ],
            }
        )

        report = scan_org_work(
            selected_repos=("ctrl-alt-keith/beta", "alpha", "ctrl-alt-keith/beta"),
            runner=gh,
        )
        text = render_text_report(report)
        data = json.loads(render_json_report(report))

        self.assertEqual(("beta", "alpha"), report.selected_repositories)
        self.assertEqual(("alpha", "beta"), tuple(repo.name for repo in report.repositories))
        self.assertNotIn("/repos/ctrl-alt-keith/gamma/pulls?state=open&per_page=100", [command[-1] for command in gh.commands])
        self.assertIn("Repository filter: beta, alpha", text)
        self.assertEqual(["beta", "alpha"], data["selected_repositories"])
        self.assertEqual(2, data["summary"]["repository_count"])

    def test_incomplete_enumeration_never_broadens_a_selected_report(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [
                    [_repo("alpha"), _repo("beta")],
                ],
                "/user/memberships/orgs/ctrl-alt-keith": {"state": "active", "role": "member"},
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

        with self.assertRaisesRegex(ValueError, "selected repositories not found in ctrl-alt-keith: missing"):
            scan_org_work(selected_repos=("missing",), runner=gh)

    def test_main_reports_unknown_selected_repository_to_stderr(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        with patch("enforcement.org_pr_issue_scan.scan_org_work", side_effect=ValueError("bad selection")):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["--repo", "missing"])

        self.assertEqual(2, code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("error: bad selection", stderr.getvalue())

    def test_empty_no_open_work_report_is_explicit(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("quiet")]],
                "/repos/ctrl-alt-keith/quiet/pulls?state=open&per_page=100": [[]],
                "/repos/ctrl-alt-keith/quiet/issues?state=open&per_page=100": [[]],
            }
        )

        report = scan_org_work(runner=gh)
        text = render_text_report(report)
        data = json.loads(render_json_report(report))

        self.assertIn("Open pull requests: 0", text)
        self.assertIn("Open issues: 0", text)
        self.assertEqual(0, data["summary"]["open_pull_request_count"])
        self.assertEqual(0, data["summary"]["open_issue_count"])

    def test_inaccessible_repository_is_reported(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": [[_repo("blocked")]],
                "/repos/ctrl-alt-keith/blocked/pulls?state=open&per_page=100": GhCommand(
                    argv=(),
                    returncode=1,
                    stdout="",
                    stderr="HTTP 403: Forbidden",
                ),
                "/repos/ctrl-alt-keith/blocked/issues?state=open&per_page=100": GhCommand(
                    argv=(),
                    returncode=1,
                    stdout="",
                    stderr="HTTP 404: Not Found",
                ),
            }
        )

        report = scan_org_work(runner=gh)
        text = render_text_report(report)

        self.assertEqual(1, len(report.repositories))
        self.assertEqual(
            [
                "pull requests inaccessible: HTTP 403: Forbidden",
                "issues inaccessible: HTTP 404: Not Found",
            ],
            report.repositories[0].skipped,
        )
        self.assertIn("skipped: pull requests inaccessible: HTTP 403: Forbidden", text)
        self.assertIn("skipped: issues inaccessible: HTTP 404: Not Found", text)

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

        advisory_code, advisory_stdout = _run_main_with_report(report)
        failing_code, failing_stdout = _run_main_with_report(report, "--fail-on-error")

        self.assertEqual(0, advisory_code)
        self.assertEqual(1, failing_code)
        self.assertIn("Skipped or partial repositories: 1", advisory_stdout)
        self.assertIn("Skipped or partial repositories: 1", failing_stdout)

    def test_fail_on_error_preserves_json_incomplete_coverage_report(self) -> None:
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

        code, stdout = _run_main_with_report(report, "--output-format", "json", "--fail-on-error")

        self.assertEqual(1, code)
        data = json.loads(stdout)
        self.assertEqual(1, data["summary"]["skipped_repository_count"])
        self.assertEqual(["pull requests inaccessible: HTTP 403: Forbidden"], data["repositories"][0]["skipped"])

    def test_fail_on_error_handles_repository_enumeration_errors(self) -> None:
        gh = FakeGh(
            {
                "/orgs/ctrl-alt-keith/repos?type=all&per_page=100": GhCommand(
                    argv=(),
                    returncode=1,
                    stdout="",
                    stderr="HTTP 500: Server Error",
                ),
            }
        )
        report = scan_org_work(runner=gh)

        advisory_code, advisory_stdout = _run_main_with_report(report)
        failing_code, failing_stdout = _run_main_with_report(report, "--fail-on-error")

        self.assertEqual(0, advisory_code)
        self.assertEqual(1, failing_code)
        self.assertIn("ERROR: repository enumeration failed: HTTP 500: Server Error", advisory_stdout)
        self.assertIn("ERROR: repository enumeration failed: HTTP 500: Server Error", failing_stdout)


class FakeGh:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> GhCommand:
        self.commands.append(argv)
        endpoint = argv[-1]
        response = self.responses.get(endpoint)
        if response is None and endpoint.startswith("/user/memberships/orgs/"):
            response = {"state": "active", "role": "admin"}
        if response is None:
            raise KeyError(endpoint)
        if isinstance(response, GhCommand):
            return response
        return GhCommand(argv=argv, returncode=0, stdout=json.dumps(response), stderr="")


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
        "default_branch": "main",
        "html_url": f"https://github.com/ctrl-alt-keith/{name}",
    }


def _item(
    number: int,
    title: str,
    *,
    author: str = "keith",
    labels: tuple[str, ...] = (),
    assignees: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/ctrl-alt-keith/sample/{number}",
        "user": {"login": author},
        "labels": [{"name": label} for label in labels],
        "assignees": [{"login": assignee} for assignee in assignees],
        "updated_at": "2026-05-08T12:00:00Z",
    }


if __name__ == "__main__":
    unittest.main()
