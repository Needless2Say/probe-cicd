"""
Scratch probe, Terraform's contract for a write only attribute on the Vercel provider.

For each round it prints the plan's exit code and, by address, the actions and the version's move, then each probe
variable's type and update time in the project, whether that time moved since the last round, and whether the
readable variable holds the value the round expects, as a yes or a no. It never prints a token or a value. The token
comes from VERCEL_TOKEN, the account from VERCEL_ORG_ID and the project from VERCEL_PROJECT_ID.
"""

from __future__ import annotations

import argparse
import json
import os

from vercel_probe import Vercel, from_ms, say, yes

KEYS = ("WO_PROBE_SECRET", "WO_PROBE_READABLE", "WO_PROBE_PLAIN")
READABLE = "WO_PROBE_READABLE"
EXIT_WORDS = {"0": "no changes", "2": "changes"}


def cmd_plan(args: argparse.Namespace) -> int:
    """
    A saved plan's actions and version moves by address, its write only value named present or absent alone.
    """
    with open(args.json, encoding = "utf-8") as handle:
        plan = json.load(handle)
    say(f"- round {args.round}, the plan's exit code {args.exit_code}, {EXIT_WORDS.get(args.exit_code, 'an error')}")
    for change in plan.get("resource_changes") or []:
        body = change.get("change") or {}
        before = body.get("before") or {}
        after = body.get("after") or {}
        carried = "absent" if after.get("value_wo") is None else "present"
        say(
            f"- {change.get('address')}: actions {'/'.join(body.get('actions') or [])}, value_wo_version "
            f"{before.get('value_wo_version')} to {after.get('value_wo_version')}, value_wo in the plan {carried}",
        )
    return 0


def cmd_env(vercel: Vercel, args: argparse.Namespace) -> int:
    """
    Each probe variable's update time against the last round's, and the readable one's value against the expected.
    """
    project = os.environ.get("VERCEL_PROJECT_ID", "")
    status, answer = vercel.call("GET", f"/v10/projects/{project}/env")
    envs = (answer.get("envs") or []) if isinstance(answer, dict) and status == 200 else []
    try:
        with open(args.state, encoding = "utf-8") as handle:
            seen = json.load(handle)
    except (OSError, ValueError):
        seen = {}
    times: dict[str, object] = {}
    for key in KEYS:
        found = [env for env in envs if isinstance(env, dict) and env.get("key") == key]
        if len(found) != 1:
            say(f"- {key}: {len(found)} found (HTTP {status})")
            continue
        env = found[0]
        times[key] = env.get("updatedAt")
        if key not in seen:
            moved = "first read"
        else:
            moved = "written" if seen[key] != times[key] else "not written"
        say(f"- {key}: type {env.get('type')}, updated {from_ms(times[key])}, since the last round {moved}")
        if key == READABLE and args.expect_readable:
            status_v, one = vercel.call("GET", f"/v1/projects/{project}/env/{env.get('id')}")
            value = one.get("value") if isinstance(one, dict) else None
            say(f"- {key} holds the value this round expects: {yes(value == args.expect_readable)} (HTTP {status_v})")
    with open(args.state, "w", encoding = "utf-8") as handle:
        json.dump(times, handle)
    return 0


def main() -> int:
    """
    The two subcommands.
    """
    parser = argparse.ArgumentParser(description = "The write only contract, names, actions and times alone.")
    sub = parser.add_subparsers(dest = "command", required = True)
    plan = sub.add_parser("plan", help = "a saved plan's actions by address")
    plan.add_argument("--json", required = True)
    plan.add_argument("--round", required = True)
    plan.add_argument("--exit-code", required = True)
    env = sub.add_parser("env", help = "the probe variables in the project")
    env.add_argument("--state", required = True)
    env.add_argument("--expect-readable", default = "")
    args = parser.parse_args()
    if args.command == "plan":
        return cmd_plan(args)
    return cmd_env(Vercel(), args)


if __name__ == "__main__":
    raise SystemExit(main())
