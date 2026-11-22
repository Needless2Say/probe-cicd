#!/usr/bin/env python3
"""
Scratch probes of GitHub Actions, a run's identity, its attempt's times and an App's dispatches.

Every subcommand prints comparisons, ids, logins and times alone, never a token. The token comes from GH_TOKEN in
the environment and is sent from this process, never on a command line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"

IDENTITY = (
    "GITHUB_EVENT_NAME", "GITHUB_ACTOR", "GITHUB_ACTOR_ID", "GITHUB_TRIGGERING_ACTOR", "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY", "GITHUB_REF", "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA",
)


def now() -> str:
    """
    The time now, UTC to the millisecond.

    Returns:
        str: the time
    """
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse(value: str | None) -> dt.datetime | None:
    """
    An ISO 8601 UTC time, to the second or the millisecond.

    Args:
        value: the time, or None

    Returns:
        dt.datetime | None: the moment, or None
    """
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def say(line: str) -> None:
    """
    Print a line, and add it to the job's summary when there is one.

    Args:
        line: the line, comparisons, ids, logins and times alone
    """
    print(line, flush = True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding = "utf-8") as handle:
            handle.write(line + "\n")


def output(name: str, value: str) -> None:
    """
    Write a step output, a public value alone.

    Args:
        name: the output's name
        value: its value
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding = "utf-8") as handle:
            handle.write(f"{name}={value}\n")


def yes(flag: bool) -> str:
    """
    A comparison's word.

    Args:
        flag: the comparison

    Returns:
        str: yes or no
    """
    return "yes" if flag else "no"


def call(method: str, path: str, body: object = None) -> tuple[int, object]:
    """
    One GitHub API call with GH_TOKEN.

    Args:
        method: the HTTP method
        path: the path after the host
        body: a JSON body, or None

    Returns:
        tuple[int, object]: the status and the parsed answer
    """
    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        say("GH_TOKEN is empty")
        sys.exit(1)
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data = data, method = method)
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout = 30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw else None
        except ValueError:
            return error.code, None


def cmd_identity(args: argparse.Namespace) -> int:
    """
    The run's identity as the runner gives it, and its actor id against the bot's and the App's ids.
    """
    say(f"- where: {args.where}")
    for name in IDENTITY:
        say(f"- {name}: `{os.environ.get(name, '')}`")
    actor_id = os.environ.get("GITHUB_ACTOR_ID", "")
    if args.bot_id:
        say(f"- GITHUB_ACTOR_ID equals the bot user's id: {yes(actor_id == args.bot_id)}")
    if args.app_id:
        say(f"- GITHUB_ACTOR_ID equals the App's id: {yes(actor_id == args.app_id)}")
    same = os.environ.get("GITHUB_ACTOR", "") == os.environ.get("GITHUB_TRIGGERING_ACTOR", "")
    say(f"- the actor and the triggering actor are one login: {yes(same)}")
    return 0


def cmd_bot_id(args: argparse.Namespace) -> int:
    """
    An App's bot user id, read by its slug, beside the App's own id.
    """
    login = args.slug + "[bot]"
    status, user = call("GET", "/users/" + urllib.parse.quote(login))
    bot_id = str(user.get("id", "")) if isinstance(user, dict) else ""
    say(f"- the bot `{login}` has user id `{bot_id}` (HTTP {status})")
    if args.app_id:
        say(f"- the bot's user id equals the App's id: {yes(bot_id == args.app_id)}")
    output("bot_id", bot_id)
    return 0 if bot_id else 1


def cmd_dispatch(args: argparse.Namespace) -> int:
    """
    Dispatch a workflow on main with the token in GH_TOKEN, an App's when the probe asks.
    """
    body = {"ref": "main", "inputs": {"bot_id": args.bot_id, "app_id": args.app_id}}
    status, _answer = call("POST", f"/repos/{args.repo}/actions/workflows/{args.workflow}/dispatches", body)
    say(f"- dispatch of {args.workflow} in {args.repo} at {now()}: HTTP {status}")
    return 0 if status in (200, 204) else 1


def cmd_rerun(args: argparse.Namespace) -> int:
    """
    Rerun a whole run with the token in GH_TOKEN.
    """
    status, _answer = call("POST", f"/repos/{args.repo}/actions/runs/{args.run}/rerun")
    say(f"- rerun of run {args.run} in {args.repo} at {now()}: HTTP {status}")
    return 0 if status in (200, 201) else 1


def cmd_attempt_times(args: argparse.Namespace) -> int:
    """
    The attempt's start as GitHub records it, against every step's start and the job's own first stamp.
    """
    base = f"/repos/{args.repo}/actions/runs/{args.run}/attempts/{args.attempt}"
    status, attempt = call("GET", base)
    attempt = attempt if isinstance(attempt, dict) else {}
    started = parse(attempt.get("run_started_at"))
    say(
        f"- attempt {args.attempt}: run_started_at {attempt.get('run_started_at')}, "
        f"created_at {attempt.get('created_at')} (HTTP {status})",
    )
    status, listing = call("GET", base + "/jobs?per_page=100")
    jobs = listing.get("jobs", []) if isinstance(listing, dict) else []
    steps = []
    for job in jobs:
        say(f"- job `{job.get('name')}` started {job.get('started_at')}")
        for step in job.get("steps") or []:
            moment = parse(step.get("started_at"))
            if moment is not None:
                steps.append((moment, job.get("name"), step.get("name")))
    # reported, never failing, so one surprise skips no later probe
    if started is None or not steps:
        say("- no attempt start or no step times to compare")
        return 0
    earliest = min(steps)
    early = [item for item in steps if item[0] < started]
    say(f"- steps read: {len(steps)}, the earliest `{earliest[2]}` at {earliest[0].isoformat()}")
    say(f"- steps that started before run_started_at: {len(early)}")
    stamp = parse(args.stamp)
    if stamp is not None:
        gap = (stamp - started).total_seconds()
        say(f"- the job's first stamp is after run_started_at: {yes(stamp >= started)}, by {gap:.3f} s")
    return 0


def main(argv: list[str]) -> int:
    """
    Parse the subcommand and run it.
    """
    parser = argparse.ArgumentParser(description = __doc__.splitlines()[1])
    sub = parser.add_subparsers(dest = "command", required = True)
    identity = sub.add_parser("identity")
    identity.add_argument("--where", default = "a job")
    identity.add_argument("--bot-id", default = "")
    identity.add_argument("--app-id", default = "")
    bot = sub.add_parser("bot-id")
    bot.add_argument("--slug", required = True)
    bot.add_argument("--app-id", default = "")
    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--repo", required = True)
    dispatch.add_argument("--workflow", required = True)
    dispatch.add_argument("--bot-id", default = "")
    dispatch.add_argument("--app-id", default = "")
    rerun = sub.add_parser("rerun")
    rerun.add_argument("--repo", required = True)
    rerun.add_argument("--run", required = True)
    times = sub.add_parser("attempt-times")
    times.add_argument("--repo", required = True)
    times.add_argument("--run", required = True)
    times.add_argument("--attempt", required = True)
    times.add_argument("--stamp", default = "")
    args = parser.parse_args(argv)
    handlers = {
        "identity": cmd_identity, "bot-id": cmd_bot_id, "dispatch": cmd_dispatch, "rerun": cmd_rerun,
        "attempt-times": cmd_attempt_times,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
