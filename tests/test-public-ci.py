#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check trusted inspection boundaries and sign-off requirements."""
import importlib.util
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_ci", ROOT / "scripts/public/ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def event():
    return {"repository": {"full_name": "example/project"}, "pull_request": {
        "number": 7, "user": {"login": "publisher"},
        "base": {"ref": "main", "sha": "a" * 40, "repo": {"full_name": "example/project"}},
        "head": {"sha": "b" * 40}}}


class Inspection(unittest.TestCase):
    def build_workflow(self):
        directory = ROOT / "scripts/public/workflows"
        if not directory.is_dir():
            directory = ROOT / ".github/workflows"
        return (directory / "check.yml").read_text()

    def test_fresh_keyring_is_initialized_before_population(self):
        workflow = self.build_workflow()
        self.assertIn("pacman-key --init", workflow)
        self.assertLess(workflow.index("pacman-key --init"),
                        workflow.index("pacman-key --populate emaki"))

    def test_container_has_explicit_user_locale_and_headless_setup(self):
        workflow = self.build_workflow()
        for requirement in ("useradd --create-home --user-group builder", "locale-gen",
                            "LANG: en_US.UTF-8", "QT_QPA_PLATFORM: offscreen",
                            "QT_QUICK_BACKEND: software", "timeout-minutes: 60",
                            "install -d -o builder -g builder -m 700"):
            self.assertIn(requirement, workflow)
        self.assertNotIn("LC_ALL:", workflow)
        for locale in ("en_US", "en_GB", "es_MX", "pt_BR", "de_DE", "fr_FR", "ru_RU"):
            self.assertIn(locale, workflow)
        for package in ("cmake", "ninja", "qt6-tools", "gettext", "noto-fonts",
                        "terminus-font", "emaki-font-iosevka"):
            self.assertIn(package, workflow)
        self.assertLess(workflow.index("useradd --create-home"), workflow.index("make paths check-core check-i18n"))

    def test_public_fork_checks_do_not_run_kitchen_workflow_assertions(self):
        workflow = self.build_workflow()
        self.assertIn("python3 tests/test-ci-forks.py ForkChecks", workflow)
        self.assertIn("python3 tests/ci-check-forks.py", workflow)
        self.assertLess(workflow.index("python3 tests/ci-check-forks.py"),
                        workflow.index("- name: Public PR subset"))
        self.assertIn("SigLevel = Required TrustedOnly", workflow)
        self.assertIn("skip fork configs:", workflow)
        self.assertIn("python3 -B tests/test-public-ci.py", workflow)

    def test_origin_uses_authenticated_pr_login(self):
        data = event()
        data["pull_request"]["head"]["user"] = {"login": "outsider"}
        self.assertEqual(ci.event_values(data, "example/project", "publisher")[1], "ours")
        data["pull_request"]["user"]["login"] = "outsider"
        self.assertEqual(ci.event_values(data, "example/project", "publisher")[1], "outside")

    def test_missing_or_invalid_publisher_is_not_outside(self):
        for login in ("", " name ", "--bad", "a/b"):
            with self.subTest(login=login), self.assertRaises(ci.Refused):
                ci.event_values(event(), "example/project", login)

    def test_rehearsal_override_requires_exact_private_nonproduction_head(self):
        data = event()
        data["repository"]["private"] = True
        for visibility in (None, "private"):
            if visibility:
                data["repository"]["visibility"] = visibility
            self.assertEqual(ci.event_values(data, "example/project", "publisher", "b" * 40)[1], "outside")
        self.assertEqual(ci.event_values(data, "example/project", "publisher", "a" * 40)[1], "ours")
        for head in ("b" * 39, "--help"):
            with self.subTest(head=head), self.assertRaises(ci.Refused):
                ci.event_values(data, "example/project", "publisher", head)
        for private, visibility in ((False, "public"), (True, "public"), (True, "internal"), (1, "private")):
            data["repository"].update(private=private, visibility=visibility)
            with self.subTest(private=private, visibility=visibility), self.assertRaises(ci.Refused):
                ci.event_values(data, "example/project", "publisher", "b" * 40)
        with self.assertRaises(ci.Refused):
            ci.event_values(event(), "example/project", "publisher", "b" * 40)
        for repository in ("emakish/emaki", "Emakish/Emaki", "emakish/.github"):
            data = event()
            data["repository"].update(full_name=repository, private=True)
            data["pull_request"]["base"]["repo"]["full_name"] = repository
            with self.subTest(repository=repository), self.assertRaises(ci.Refused):
                ci.event_values(data, repository, "publisher", "b" * 40)

    def test_event_cannot_request_outside_rehearsal(self):
        data = event()
        data["repository"].update(private=True, PUBLIC_REHEARSAL_OUTSIDE_HEAD="b" * 40)
        data["pull_request"]["rehearsal_outside_head"] = "b" * 40
        self.assertEqual(ci.event_values(data, "example/project", "publisher")[1], "ours")

    def test_repository_ref_and_oid_confusion_refused(self):
        for field, value in (("ref", "release"), ("sha", "--help"), ("sha", "a" * 39)):
            data = event()
            data["pull_request"]["base"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ci.Refused):
                ci.event_values(data, "example/project", "publisher")
        data = event()
        data["pull_request"]["base"]["repo"]["full_name"] = "example/elsewhere"
        with self.assertRaises(ci.Refused):
            ci.event_values(data, "example/project", "publisher")

    def test_boolean_or_negative_pr_number_refused(self):
        for value in (True, -1, "7"):
            data = event()
            data["pull_request"]["number"] = value
            with self.subTest(value=value), self.assertRaises(ci.Refused):
                ci.event_values(data, "example/project", "publisher")

    def test_outside_wording_flag_requires_visible_decision(self):
        result = ci.check_result(2, "outside")
        self.assertEqual(result[0], "success")
        self.assertIn("maintainer decision", result[1])
        self.assertEqual(ci.check_result(2, "ours")[0], "failure")
        for code in (1, 3, -9):
            self.assertEqual(ci.check_result(code, "outside")[0], "failure")

    def test_matching_signoff_in_trailer_block(self):
        record = "Example Person\0person@example.com\0Fix display\n\nSigned-off-by: Example Person <person@example.com>\nNote: none\n"
        with patch.object(ci, "git", side_effect=["b" * 40 + "\n", record]):
            self.assertEqual(ci.signoff_failures(Path("."), "a" * 40, "b" * 40), [])

    def test_quoted_or_mismatched_signoff_is_rejected(self):
        for message in ("Fix\n\nSigned-off-by: Someone Else <person@example.com>",
                        "Fix\n\nSigned-off-by: Example Person <person@example.com>\n\nExplanation",
                        "Fix\n\nSigned-off-by: Example Person <person@example.com>\nFree prose"):
            record = "Example Person\0person@example.com\0" + message
            with self.subTest(message=message), patch.object(ci, "git", side_effect=["b" * 40 + "\n", record]):
                self.assertEqual(ci.signoff_failures(Path("."), "a" * 40, "b" * 40), ["b" * 40])

    def test_every_commit_needs_its_own_signoff(self):
        good = "Example Person\0person@example.com\0Fix\n\nSigned-off-by: Example Person <person@example.com>\n"
        bad = "Other Person\0other@example.com\0Fix\n"
        with patch.object(ci, "git", side_effect=["b" * 40 + "\n" + "c" * 40, good, bad]):
            self.assertEqual(ci.signoff_failures(Path("."), "a" * 40, "c" * 40), ["c" * 40])

    def test_empty_range_cannot_pass(self):
        with patch.object(ci, "git", return_value=""), self.assertRaises(ci.Refused):
            ci.signoff_failures(Path("."), "a" * 40, "b" * 40)

    def inspect_fixture(self, *, changed=False, approved="", result=0, rehearsal="", signoffs=None):
        data = event()
        data["repository"]["private"] = True
        current = copy.deepcopy(data["pull_request"])
        if changed:
            current["body"] = "New text after inspection began"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "event.json").write_text(json.dumps(data))
            args = SimpleNamespace(event=path / "event.json", repository="example/project", repo=path,
                                   publisher_login="publisher", report_checks=True,
                                   approved_infrastructure_head=approved, rehearsal_outside_head=rehearsal)
            calls = []
            def request(repository, token, route, method, payload):
                calls.append((route, method, payload))
                if method == "POST":
                    return {"id": len(calls)}
                if method == "GET":
                    return current
                return {}
            with patch.object(ci, "git", side_effect=["a" * 40, "", "b" * 40, ""]), \
                    patch.object(ci, "api", side_effect=request), \
                    patch.object(ci, "signoff_failures", return_value=signoffs or []), \
                    patch.object(ci.subprocess, "run", return_value=SimpleNamespace(returncode=result)) as run, \
                    patch.dict(ci.os.environ, {"GH_TOKEN": "synthetic"}):
                error = None
                try:
                    outcome = ci.inspect(args)
                except ci.Refused as exception:
                    outcome, error = 1, exception
            return outcome, error, calls, run.call_args

    def test_reported_checks_bind_exact_head_without_exposing_credential(self):
        outcome, error, calls, command = self.inspect_fixture()
        self.assertEqual(outcome, 0)
        self.assertIsNone(error)
        created = [payload for route, method, payload in calls if method == "POST"]
        self.assertEqual({payload["name"] for payload in created}, {"public-guard", "dco"})
        self.assertTrue(all(payload["head_sha"] == "b" * 40 for payload in created))
        self.assertNotIn("GH_TOKEN", command.kwargs["env"])
        self.assertTrue(all(payload["conclusion"] == "success" for route, method, payload in calls if method == "PATCH"))

    def test_metadata_race_cannot_report_success(self):
        outcome, error, calls, _ = self.inspect_fixture(changed=True)
        self.assertEqual(outcome, 1)
        self.assertIsInstance(error, ci.Refused)
        self.assertTrue(all(payload["conclusion"] == "failure" for route, method, payload in calls if method == "PATCH"))

    def test_reviewed_head_is_explicit_and_never_comes_from_event(self):
        _, _, _, command = self.inspect_fixture(approved="b" * 40)
        self.assertIn("--approved-infrastructure-head", command.args[0])
        _, _, _, ordinary = self.inspect_fixture()
        self.assertNotIn("--approved-infrastructure-head", ordinary.args[0])

    def test_guard_failure_leaves_signoff_separate(self):
        outcome, _, calls, _ = self.inspect_fixture(result=1)
        self.assertEqual(outcome, 1)
        conclusions = [payload["conclusion"] for route, method, payload in calls if method == "PATCH"]
        self.assertEqual(conclusions, ["failure", "success"])

    def test_rehearsal_uses_normal_outside_guard_and_still_requires_signoff(self):
        outcome, _, calls, command = self.inspect_fixture(rehearsal="b" * 40, result=2)
        self.assertEqual(outcome, 0)
        arguments = command.args[0]
        self.assertEqual(arguments[arguments.index("--origin") + 1], "outside")
        self.assertTrue(any("FLAG" in payload["output"]["summary"]
                            for _, method, payload in calls if method == "PATCH"))
        outcome, _, calls, _ = self.inspect_fixture(rehearsal="b" * 40, signoffs=["b" * 40])
        self.assertEqual(outcome, 1)
        self.assertEqual([payload["conclusion"] for _, method, payload in calls if method == "PATCH"],
                         ["success", "failure"])

    def test_workflows_keep_privileged_and_candidate_execution_separate(self):
        workflow_dir = ROOT / "scripts/public/workflows"
        if not workflow_dir.is_dir():
            workflow_dir = ROOT / ".github/workflows"
        guard = (workflow_dir / "guard.yml").read_text()
        build = (workflow_dir / "check.yml").read_text()
        self.assertIn("pull_request_target:", guard)
        self.assertIn("pull_request.base.sha", guard)
        self.assertNotIn("pull_request.head.sha", guard)
        self.assertNotIn("make check", guard)
        self.assertIn("checks: write", guard)
        self.assertIn("PUBLIC_REHEARSAL_OUTSIDE_HEAD: ${{ vars.PUBLIC_REHEARSAL_OUTSIDE_HEAD }}", guard)
        self.assertIn('--rehearsal-outside-head "$PUBLIC_REHEARSAL_OUTSIDE_HEAD"', guard)
        self.assertIn("pull_request:", build)
        self.assertNotIn("checks: write", build)
        self.assertNotIn("secrets.", build)
        for workflow in (guard, build):
            self.assertIn("persist-credentials: false", workflow)
            self.assertIn("contents: read", workflow)


if __name__ == "__main__":
    unittest.main()
