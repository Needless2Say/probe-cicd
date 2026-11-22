#!/usr/bin/env python3
"""
Scratch probes of Vercel's deployments, aliases and settings.

Every subcommand prints comparisons, ids, hostnames and times alone. It never prints a token, a bypass value or a
setting's value, and it never prints a whole API response, since a project's and an alias's responses carry the
project's bypass values. The token comes from VERCEL_TOKEN and the bypass value from VERCEL_AUTOMATION_BYPASS_SECRET,
each read from the environment and sent from this process, never on a command line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.vercel.com"

# the scratch values a setting can hold, so every route and line names a value and never shows it
NAMES = ("one", "two", "three", "four", "five")
VALUES = {
    "PROBE_SETTING": {name: f"probe-setting-{name}" for name in NAMES},
    "NEXT_PUBLIC_PROBE_SETTING": {name: f"probe-setting-{name}" for name in NAMES},
    "PROBE_SENSITIVE": {"present": "probe-sensitive-present"},
    "CRON_SECRET": {name: f"probe-cron-{name}" for name in NAMES},
}

PROJECTS = {"py": ("probe-py", "fastapi"), "next": ("probe-next", "nextjs")}

# Vercel's API name for the protection of every deployment URL but the production domains
STANDARD_PROTECTION = "prod_deployment_urls_and_all_previews"

STAMP_KEYS = ("kdf_version", "kdf_commit", "kdf_run_id", "kdf_run_attempt", "kdf_started_at")


def now() -> str:
    """
    The time now, UTC to the millisecond.

    Returns:
        str: the time in the form the stamps use
    """
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def from_ms(value: object) -> str:
    """
    A time Vercel gives in milliseconds, written as the stamps write a time.

    Args:
        value: milliseconds since the epoch, or anything else

    Returns:
        str: the time, or "none"
    """
    if not isinstance(value, (int, float)):
        return "none"
    moment = dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def say(line: str) -> None:
    """
    Print a line, and add it to the job's summary when there is one.

    Args:
        line: the line, comparisons, ids, hostnames and times alone
    """
    print(line, flush = True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding = "utf-8") as handle:
            handle.write(line + "\n")


def yes(flag: bool) -> str:
    """
    A comparison's word.

    Args:
        flag: the comparison

    Returns:
        str: yes or no
    """
    return "yes" if flag else "no"


def host_of(url: str) -> str:
    """
    A URL's hostname, or the value itself when it is one already.

    Args:
        url: a URL or a hostname

    Returns:
        str: the hostname
    """
    return urllib.parse.urlparse(url).hostname or url


def error_code(payload: object) -> str:
    """
    Vercel's error code from an error answer, never its message.

    Args:
        payload: the parsed answer

    Returns:
        str: the code, or "none"
    """
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return str(payload["error"].get("code", "none"))
    return "none"


class Vercel:
    """
    Vercel's REST API through one token, scoped to a team only when the account id names one.
    """

    def __init__(self) -> None:
        token = os.environ.get("VERCEL_TOKEN", "").strip()
        if not token:
            say("VERCEL_TOKEN is empty")
            sys.exit(1)
        self._token = token
        org = os.environ.get("VERCEL_ORG_ID", "").strip()
        self.scope = {"teamId": org} if org.startswith("team_") else {}

    def call(self, method: str, path: str, body: object = None, query: dict | None = None) -> tuple[int, object]:
        """
        One API call.

        Args:
            method: the HTTP method
            path: the path after the host, its version first
            body: a JSON body, or None
            query: query parameters beside the scope

        Returns:
            tuple[int, object]: the status and the parsed answer
        """
        params = dict(self.scope)
        params.update(query or {})
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data = data, method = method)
        request.add_header("Authorization", "Bearer " + self._token)
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """
    Hands a redirect back as an answer, so a protected URL's redirect to Vercel's sign in is seen.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802, PLR0913
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, bypass: bool) -> tuple[int, str, str, dict]:
    """
    One GET of a deployment's route.

    Args:
        url: the full URL
        bypass: whether to send the project's bypass value from the environment

    Returns:
        tuple[int, str, str, dict]: the status, the redirect's host, Vercel's error code and the parsed JSON body
    """
    request = urllib.request.Request(url, method = "GET")
    value = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
    if bypass and value:
        request.add_header("x-vercel-protection-bypass", value)
    try:
        with _OPENER.open(request, timeout = 60) as response:
            status, headers, raw = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        status, headers, raw = error.code, error.headers, error.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, "", "", {}
    location = host_of(headers.get("Location", "")) if headers.get("Location") else ""
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return status, location, headers.get("x-vercel-error", ""), body if isinstance(body, dict) else {}


def project_names(which: str) -> list[str]:
    """
    The scratch projects a subcommand acts on.

    Args:
        which: py, next or both

    Returns:
        list[str]: their names
    """
    keys = ["py", "next"] if which == "both" else [which]
    return [PROJECTS[key][0] for key in keys]


def cmd_setup(vercel: Vercel, _args: argparse.Namespace) -> int:
    """
    Make each scratch project when absent, set Node 24 on the Next.js one and Standard Protection where unset.
    """
    status, user = vercel.call("GET", "/v2/user")
    who = user.get("user", {}) if isinstance(user, dict) else {}
    say(f"- the token acts as user `{who.get('id')}`, default team `{who.get('defaultTeamId')}` (HTTP {status})")
    failed = 0
    for key, (name, framework) in PROJECTS.items():
        status, project = vercel.call("GET", f"/v9/projects/{name}")
        if status == 404:
            status, project = vercel.call("POST", "/v11/projects", {"name": name, "framework": framework})
            say(f"- {name}: made, HTTP {status} {error_code(project)}")
        if status not in (200, 201) or not isinstance(project, dict):
            say(f"- {name}: not read, HTTP {status} {error_code(project)}")
            failed += 1
            continue
        patch: dict = {}
        if key == "next" and project.get("nodeVersion") != "24.x":
            patch["nodeVersion"] = "24.x"
        protection = (project.get("ssoProtection") or {}).get("deploymentType")
        if not protection:
            patch["ssoProtection"] = {"deploymentType": STANDARD_PROTECTION}
        if patch:
            status, patched = vercel.call("PATCH", f"/v9/projects/{project['id']}", patch)
            say(f"- {name}: set {sorted(patch)}, HTTP {status} {error_code(patched)}")
            if isinstance(patched, dict) and status == 200:
                project = patched
        protection = (project.get("ssoProtection") or {}).get("deploymentType")
        say(
            f"- {name}: id `{project.get('id')}`, account `{project.get('accountId')}`, framework "
            f"`{project.get('framework')}`, node `{project.get('nodeVersion')}`, protection `{protection}`",
        )
    return 1 if failed else 0


def cmd_env(vercel: Vercel, args: argparse.Namespace) -> int:
    """
    Set a scratch setting to a named value, or delete it, on one project or both.
    """
    failed = 0
    for name in project_names(args.project):
        if args.delete:
            status, listing = vercel.call("GET", f"/v9/projects/{name}/env")
            envs = listing.get("envs", []) if isinstance(listing, dict) else []
            ids = [env.get("id") for env in envs if isinstance(env, dict) and env.get("key") == args.key]
            for env_id in ids:
                status, answer = vercel.call("DELETE", f"/v9/projects/{name}/env/{env_id}")
                say(f"- {name}: {args.key} deleted at {now()}, HTTP {status} {error_code(answer)}")
                failed += status not in (200, 204)
            if not ids:
                say(f"- {name}: {args.key} was absent at {now()}")
            continue
        value = VALUES[args.key].get(args.value)
        if value is None:
            say(f"- {args.key} takes {sorted(VALUES[args.key])}, not `{args.value}`")
            return 1
        body = {"key": args.key, "value": value, "type": args.type, "target": ["production", "preview"]}
        status, answer = vercel.call("POST", f"/v10/projects/{name}/env", body, {"upsert": "true"})
        say(f"- {name}: {args.key} set to `{args.value}`, {args.type}, at {now()}, HTTP {status} {error_code(answer)}")
        failed += status not in (200, 201)
    return 1 if failed else 0


def deployment(vercel: Vercel, id_or_host: str) -> tuple[int, dict]:
    """
    A deployment by its id or a hostname.

    Args:
        vercel: the API
        id_or_host: a deployment id or hostname

    Returns:
        tuple[int, dict]: the status and the deployment, empty when not read
    """
    status, answer = vercel.call("GET", f"/v13/deployments/{id_or_host}")
    return status, answer if isinstance(answer, dict) and status == 200 else {}


def live(vercel: Vercel, project: str) -> tuple[str, dict]:
    """
    The project's production target, read from the project and then as a deployment.

    Args:
        vercel: the API
        project: the project's id or name

    Returns:
        tuple[str, dict]: the target's id and its deployment, both empty when there is none
    """
    status, answer = vercel.call("GET", f"/v9/projects/{project}")
    target = ((answer.get("targets") or {}).get("production") or {}) if isinstance(answer, dict) else {}
    target_id = target.get("id") or ""
    if not target_id:
        return "", {}
    return target_id, deployment(vercel, target_id)[1]


def cmd_target(vercel: Vercel, args: argparse.Namespace) -> int:
    """
    Resolve the candidate, the project's production target and every alias, and hold the stamp to what was sent.
    """
    project = os.environ.get("VERCEL_PROJECT_ID", "")
    status, candidate = deployment(vercel, host_of(args.url))
    if not candidate:
        # reported, never failing, so one surprise skips no later probe
        say(f"- the candidate was not read, HTTP {status}")
        return 0
    say(
        f"- candidate `{candidate.get('id')}`, created {from_ms(candidate.get('createdAt'))}, ready "
        f"{from_ms(candidate.get('ready'))}, state {candidate.get('readyState')}, target {candidate.get('target')}, "
        f"alias assigned {candidate.get('aliasAssigned')}",
    )
    meta = candidate.get("meta") or {}
    expected = {
        "kdf_version": args.version,
        "kdf_commit": os.environ.get("GITHUB_SHA", ""),
        "kdf_run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "kdf_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "kdf_started_at": args.started,
    }
    for key in STAMP_KEYS:
        say(f"- meta `{key}` came back as sent: {yes(meta.get(key) == expected[key])}")
    target_id, _target = live(vercel, project)
    say(f"- the project's targets.production names the candidate: {yes(target_id == candidate.get('id'))}")
    status, answer = vercel.call("GET", f"/v9/projects/{project}")
    production = ((answer.get("targets") or {}).get("production") or {}) if isinstance(answer, dict) else {}
    say(f"- targets.production carries the fields {sorted(production)}")
    stamped = (production.get("meta") or {}).get("kdf_started_at") == args.started
    say(f"- targets.production carries the stamp: {yes(stamped)}")
    for alias in candidate.get("alias") or []:
        status_a, by_alias = vercel.call("GET", f"/v4/aliases/{alias}")
        by_alias = by_alias if isinstance(by_alias, dict) else {}
        alias_meta = (by_alias.get("deployment") or {}).get("meta") or {}
        status_d, by_host = deployment(vercel, alias)
        say(
            f"- alias {alias}, the alias endpoint names the candidate: "
            f"{yes(by_alias.get('deploymentId') == candidate.get('id'))} (HTTP {status_a}), with its stamp: "
            f"{yes(alias_meta.get('kdf_started_at') == args.started)}, the deployment endpoint read by this hostname "
            f"names it: {yes(by_host.get('id') == candidate.get('id'))} (HTTP {status_d})",
        )
    return 0


def cmd_fresh(vercel: Vercel, args: argparse.Namespace) -> int:
    """
    The freshness rule's comparison, the candidate's stamped start against the live deployment's.
    """
    project = os.environ.get("VERCEL_PROJECT_ID", "")
    target_id, target = live(vercel, project)
    if not target_id:
        verdict = "no live deployment"
    else:
        started = (target.get("meta") or {}).get("kdf_started_at")
        if not started:
            verdict = "live unstamped"
        elif args.started < started:
            verdict = "older"
        elif args.started == started:
            verdict = "equal"
        else:
            verdict = "newer"
    say(f"- the candidate's stamped start against the live deployment `{target_id or 'none'}`: {verdict}")
    if args.expect:
        # reported, never failing, so one surprise skips no later probe
        say(f"- the verdict is the one expected, {args.expect}: {yes(verdict == args.expect)}")
    return 0


def cmd_smoke(_vercel: Vercel | None, args: argparse.Namespace) -> int:
    """
    A route through the bypass and without it, the first expected to answer as asked, the second to be protected.
    """
    url = args.url.rstrip("/") + args.path
    status = 0
    for attempt in range(1, 7):
        status, location, platform, body = fetch(url, bypass = True)
        say(f"- attempt {attempt}, through the bypass: HTTP {status}{' ' + platform if platform else ''}")
        if status == 200 or (args.expect == "error" and status >= 500):
            break
        time.sleep(10)
    passed = status == 200 if args.expect == "ok" else status >= 500
    plain, location, _platform, _body = fetch(url, bypass = False)
    protected = plain == 401 or location.endswith("vercel.com")
    say(f"- without the bypass: HTTP {plain}, protected: {yes(protected)}")
    say(f"- the smoke answered as expected, {args.expect}: {yes(passed)}")
    return 0 if passed else 1


def cmd_setting(_vercel: Vercel | None, args: argparse.Namespace) -> int:
    """
    What a deployment's route reads, by name, beside what was expected, never failing.
    """
    status, _location, _platform, body = fetch(args.url.rstrip("/") + args.path, bypass = True)
    for field, expected in (("setting", args.expect_setting), ("public", args.expect_public)):
        if field in body:
            match = f", the one expected, {expected}: {yes(body[field] == expected)}" if expected else ""
            say(f"- {field} reads `{body[field]}`{match}")
    if "sensitive" in body:
        say(f"- the sensitive setting is {body['sensitive']}")
    if status != 200:
        say(f"- the route answered HTTP {status}")
    return 0


def cmd_watch(vercel: Vercel, args: argparse.Namespace) -> int:
    """
    After a promotion, when the API first shows the aliases and when each alias first serves the candidate.
    """
    host = host_of(args.url)
    began = time.monotonic()
    candidate: dict = {}
    while time.monotonic() - began < args.timeout:
        _status, candidate = deployment(vercel, host)
        if candidate.get("aliasAssigned") and candidate.get("alias"):
            break
        time.sleep(2)
    aliases = list(candidate.get("alias") or [])
    say(f"- the API showed {len(aliases)} aliases at {now()}: {', '.join(aliases) or 'none'}")
    first: dict[str, str] = {}
    while time.monotonic() - began < args.timeout and len(first) < len(aliases):
        for alias in aliases:
            if alias in first:
                continue
            status, _location, _platform, body = fetch(f"https://{alias}{args.path}", bypass = True)
            if status == 200 and body.get("vercel_url") == host:
                first[alias] = now()
        time.sleep(2)
    for alias in aliases:
        say(f"- {alias} first served the candidate at {first.get(alias, 'no time within the wait')}")
    # reported, never failing, so one surprise skips no later probe
    return 0


def main(argv: list[str]) -> int:
    """
    Parse the subcommand and run it.
    """
    parser = argparse.ArgumentParser(description = __doc__.splitlines()[1])
    sub = parser.add_subparsers(dest = "command", required = True)
    sub.add_parser("setup")
    env = sub.add_parser("env")
    env.add_argument("--project", choices = ["py", "next", "both"], required = True)
    env.add_argument("--key", choices = sorted(VALUES), required = True)
    env.add_argument("--value", default = "one")
    env.add_argument("--type", choices = ["plain", "encrypted", "sensitive"], default = "plain")
    env.add_argument("--delete", action = "store_true")
    target = sub.add_parser("target")
    target.add_argument("--url", required = True)
    target.add_argument("--version", required = True)
    target.add_argument("--started", required = True)
    fresh = sub.add_parser("fresh")
    fresh.add_argument("--started", required = True)
    fresh.add_argument("--expect", choices = ["no live deployment", "live unstamped", "older", "equal", "newer"])
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--url", required = True)
    smoke.add_argument("--path", required = True)
    smoke.add_argument("--expect", choices = ["ok", "error"], default = "ok")
    setting = sub.add_parser("setting")
    setting.add_argument("--url", required = True)
    setting.add_argument("--path", required = True)
    setting.add_argument("--expect-setting", default = "")
    setting.add_argument("--expect-public", default = "")
    watch = sub.add_parser("watch")
    watch.add_argument("--url", required = True)
    watch.add_argument("--path", required = True)
    watch.add_argument("--timeout", type = int, default = 240)
    args = parser.parse_args(argv)
    handlers = {
        "setup": cmd_setup, "env": cmd_env, "target": cmd_target, "fresh": cmd_fresh,
        "smoke": cmd_smoke, "setting": cmd_setting, "watch": cmd_watch,
    }
    needs_api = args.command not in ("smoke", "setting")
    return handlers[args.command](Vercel() if needs_api else None, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
