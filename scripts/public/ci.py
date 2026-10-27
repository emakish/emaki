#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect pull request objects without executing their contents."""
import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


class Refused(Exception):
    pass


def rules_input():
    value = os.environ.get("PUBLIC_GUARD_RULES", "")
    try:
        raw = base64.b64decode(value, validate=True)
        if not raw or base64.b64encode(raw).decode("ascii") != value:
            raise ValueError
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError
    except (ValueError, UnicodeError, binascii.Error):
        raise Refused("Required check configuration is unavailable") from None
    return raw


def child_environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
            and key not in {"GH_TOKEN", "GITHUB_TOKEN", "PUBLIC_GUARD_RULES"}}


def event_values(event, repository, publisher, rehearsal_outside_head=""):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise Refused("Invalid repository identity")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?", publisher):
        raise Refused("A trusted publisher login must be configured")
    pr = event["pull_request"]
    if event["repository"]["full_name"] != repository or pr["base"]["repo"]["full_name"] != repository:
        raise Refused("Pull request repository mismatch")
    if pr["base"]["ref"] != "main" or type(pr["number"]) is not int or pr["number"] < 1:
        raise Refused("Only numbered pull requests to main are accepted")
    for side in ("base", "head"):
        if not re.fullmatch(r"[0-9a-f]{40}", pr[side]["sha"]):
            raise Refused("Invalid commit identifier")
    origin = "ours" if pr["user"]["login"].casefold() == publisher.casefold() else "outside"
    if rehearsal_outside_head:
        target = event["repository"]
        if (not re.fullmatch(r"[0-9a-f]{40}", rehearsal_outside_head)
                or target.get("private") is not True
                or target.get("visibility", "private") != "private"
                or repository.casefold() in {"emakish/emaki", "emakish/.github"}):
            raise Refused("Outside authorship rehearsal requires an exact private nonproduction commit")
        if rehearsal_outside_head == pr["head"]["sha"]:
            origin = "outside"
    return pr, origin


def git(repo, *args, token=None):
    env = child_environment()
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    if token:
        value = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + value)
    result = subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=" + os.devnull,
                             "-c", "core.fsmonitor=false", "-c", "http.followRedirects=false", *args],
                            env=env, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise Refused("Cannot inspect the requested Git objects")
    return result.stdout


def signoff_failures(repo, base, head):
    commits = git(repo, "rev-list", "--reverse", base + ".." + head).splitlines()
    if not commits:
        raise Refused("The pull request contains no new commits")
    failures = []
    for commit in commits:
        record = git(repo, "show", "-s", "--format=%an%x00%ae%x00%B", commit)
        name, email, message = record.split("\0", 2)
        # A sign-off must be in the final trailer paragraph, not quoted in prose.
        paragraph = message.rstrip().rsplit("\n\n", 1)[-1]
        expected = "signed-off-by: " + name + " <" + email + ">"
        trailers = paragraph.splitlines()
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z-]*: .+", line) for line in trailers):
            failures.append(commit)
        elif expected.casefold() not in [line.casefold() for line in trailers]:
            failures.append(commit)
    return failures


def check_result(code, origin):
    if code == 0:
        return "success", "Publication policy passed."
    if code == 2 and origin == "outside":
        return "success", "FLAG: wording requires a maintainer decision before approval."
    return "failure", "Publication policy requires corrections or a reviewed policy update."


def api(repository, token, route, method, payload):
    request = urllib.request.Request("https://api.github.com/repos/" + repository + route,
                                    data=json.dumps(payload).encode() if payload is not None else None, method=method,
                                    headers={"Authorization": "Bearer " + token,
                                             "Accept": "application/vnd.github+json",
                                             "X-GitHub-Api-Version": "2022-11-28",
                                             "Content-Type": "application/json"})
    # Redirecting an authenticated write is never permitted.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, ValueError):
        raise Refused("Cannot report the check result") from None


def inspect(args):
    event = json.loads(Path(args.event).read_text())
    pr, origin = event_values(event, args.repository, args.publisher_login, args.rehearsal_outside_head)
    repo = Path(args.repo).resolve()
    trusted = Path(__file__).resolve().parent
    if git(repo, "rev-parse", "HEAD").strip() != pr["base"]["sha"]:
        raise Refused("The inspected checkout must be the trusted base commit")
    token = os.environ.get("GH_TOKEN", "")
    if args.report_checks and not token:
        raise Refused("Check reporting requires a job-scoped credential")
    check_ids = {}
    results = {name: ("failure", "Inspection did not complete.") for name in ("public-guard", "dco")}
    try:
        if args.report_checks:
            for name in results:
                response = api(args.repository, token, "/check-runs", "POST",
                               {"name": name, "head_sha": pr["head"]["sha"], "status": "in_progress"})
                check_ids[name] = response["id"]
        rules = rules_input()
        ref = "refs/public-check/" + str(pr["number"])
        git(repo, "fetch", "--no-tags", "https://github.com/" + args.repository + ".git",
            "+refs/pull/" + str(pr["number"]) + "/head:" + ref, token=token)
        if git(repo, "rev-parse", ref).strip() != pr["head"]["sha"]:
            raise Refused("The pull request changed during inspection")
        git(repo, "merge-base", "--is-ancestor", pr["base"]["sha"], pr["head"]["sha"])
        with tempfile.TemporaryDirectory(prefix="public-check-") as directory:
            stage = Path(directory)
            (stage / "title").write_text(pr.get("title") or "")
            (stage / "body").write_text(pr.get("body") or "")
            env = child_environment()
            command = [sys.executable, "-B", str(trusted / "guard.py"),
                                     "--repo", str(repo), "--base", pr["base"]["sha"],
                                     "--head", pr["head"]["sha"], "--origin", origin,
                                     "--pr-title-file", str(stage / "title"),
                                     "--pr-body-file", str(stage / "body"),
                                     "--json", str(stage / "guard.json"), "--rules-stdin"]
            if args.approved_infrastructure_head:
                if not re.fullmatch(r"[0-9a-f]{40}", args.approved_infrastructure_head):
                    raise Refused("Invalid reviewed infrastructure commit")
                command.extend(["--approved-infrastructure-head", args.approved_infrastructure_head])
            result = subprocess.run(command, env=env, input=rules, capture_output=True, timeout=600)
            results["public-guard"] = check_result(result.returncode, origin)
            if (stage / "guard.json").exists():
                report = json.loads((stage / "guard.json").read_text())
                # Public output contains rule identifiers and counts, never rejected text.
                counts = {}
                for finding in report.get("findings", []):
                    rule = finding.get("rule", "policy")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", rule):
                        rule = "policy"
                    counts[rule] = counts.get(rule, 0) + 1
                conclusion, summary = results["public-guard"]
                results["public-guard"] = (conclusion, summary + "\n\nRule counts: " + json.dumps(counts))
        failures = signoff_failures(repo, pr["base"]["sha"], pr["head"]["sha"])
        results["dco"] = ("failure", "Every commit needs its author's matching sign-off.") if failures else (
            "success", "All commit authors supplied matching sign-offs.")
        if args.report_checks:
            current = api(args.repository, token, "/pulls/" + str(pr["number"]), "GET", None)
            same = all(current[side]["sha"] == pr[side]["sha"] for side in ("head", "base"))
            same &= all((current.get(key) or "") == (pr.get(key) or "") for key in ("title", "body"))
            same &= current["user"]["login"] == pr["user"]["login"]
            if not same:
                results = {name: ("failure", "The pull request changed; fresh checks are required.") for name in results}
                raise Refused("Pull request metadata changed during inspection")
    finally:
        for name, check_id in check_ids.items():
            conclusion, summary = results[name]
            api(args.repository, token, "/check-runs/" + str(check_id), "PATCH",
                {"status": "completed", "conclusion": conclusion,
                 "output": {"title": name, "summary": summary}})
    for name, (conclusion, summary) in results.items():
        print(name + ": " + conclusion + " — " + summary)
    return 0 if all(result[0] == "success" for result in results.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("event", "repo", "repository", "publisher-login"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--report-checks", action="store_true")
    parser.add_argument("--approved-infrastructure-head", default="")
    parser.add_argument("--rehearsal-outside-head", default="")
    try:
        return inspect(parser.parse_args())
    except (Refused, KeyError, ValueError, OSError, subprocess.TimeoutExpired):
        print("REFUSED: publication inspection could not complete.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
