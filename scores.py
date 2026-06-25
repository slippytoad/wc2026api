#!/usr/bin/env python3
"""
WC 2026 scores – wc2026api.com
Usage:
    export WC2026_API_KEY="your_key_here"
    python scores.py                   # live matches
    python scores.py --today           # today's fixtures
    python scores.py --results         # latest completed matches
    python scores.py --standings A     # group standings (computed)
    python scores.py --team MEX        # all matches for a team
"""

import os
import sys
import json
import ssl
import argparse
from datetime import datetime, timezone
import urllib.request
import urllib.parse

BASE = "https://api.wc2026api.com"

LIVE_PHASES = {"1H", "HT", "2H", "ET1", "ET2", "PEN"}

_CA_BUNDLE = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
_ssl_ctx = ssl.create_default_context(cafile=_CA_BUNDLE) if _CA_BUNDLE else ssl.create_default_context()
_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"https": _proxy} if _proxy else {}),
    urllib.request.HTTPSHandler(context=_ssl_ctx),
)


def api_get(path: str, params: dict = None) -> list | dict:
    key = os.environ.get("WC2026_API_KEY")
    if not key:
        sys.exit("Error: WC2026_API_KEY environment variable not set.")
    qs = urllib.parse.urlencode(params or {})
    url = f"{BASE}/{path}{'?' + qs if qs else ''}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    with _opener.open(req, timeout=10) as resp:
        return json.loads(resp.read())


def fmt_kickoff(utc_str: str) -> str:
    """Return kickoff as local-ish UTC string, e.g. 'Jun 22 19:00'."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%Mz")
    except Exception:
        return utc_str


def fmt_score(m: dict) -> str:
    hs, as_ = m.get("home_score"), m.get("away_score")
    if hs is None:
        return "  vs "
    hp, ap = m.get("home_pen"), m.get("away_pen")
    s = f"{hs} - {as_}"
    if hp is not None:
        s += f" (p: {hp}-{ap})"
    return s


def fmt_match(m: dict, show_group: bool = True) -> str:
    home = m.get("home_team", "?")
    away = m.get("away_team", "?")
    score = fmt_score(m)
    phase = m.get("phase", m.get("status", ""))
    group = f"Grp {m['group_name']}" if show_group and m.get("group_name") else ""
    kickoff = fmt_kickoff(m.get("kickoff_utc", ""))
    stadium = m.get("stadium", "")
    label = phase if phase in LIVE_PHASES else kickoff
    return f"  {home:>24}  {score:^11}  {away:<24}  [{label}]  {group}  {stadium}"


def cmd_live():
    matches = api_get("matches", {"status": "live"})
    if not matches:
        print("No live matches right now.")
        return
    n = len(matches)
    print(f"⚽  LIVE  ({n} match{'es' if n != 1 else ''})\n")
    for m in matches:
        print(fmt_match(m))


def cmd_check():
    """Show matches completed in the last hour and kicking off in the next hour."""
    now = datetime.now(timezone.utc)
    all_matches = api_get("matches")

    recent, live, upcoming = [], [], []
    for m in all_matches:
        ko_str = m.get("kickoff_utc", "")
        if not ko_str:
            continue
        ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
        phase = m.get("phase", "")
        status = m.get("status", "")

        # Currently in progress
        if phase in LIVE_PHASES:
            live.append(m)

        # Completed in the last hour: estimate finish as kickoff + ~2h
        elif status == "completed" and phase in ("FT", "FT_PEN"):
            finished_approx = ko.timestamp() + 7200
            if 0 <= now.timestamp() - finished_approx <= 3600:
                recent.append(m)

        # Kicking off in the next hour
        elif phase == "PRE" or status == "scheduled":
            delta = (ko - now).total_seconds()
            if 0 <= delta <= 3600:
                upcoming.append(m)

    found = False
    if live:
        found = True
        print(f"⚽  IN PROGRESS ({len(live)})\n")
        for m in sorted(live, key=lambda x: x.get("kickoff_utc", "")):
            print(fmt_match(m))
        print()

    if recent:
        found = True
        print(f"🏁  FINISHED IN LAST HOUR ({len(recent)})\n")
        for m in sorted(recent, key=lambda x: x.get("kickoff_utc", ""), reverse=True):
            print(fmt_match(m))
        print()

    if upcoming:
        found = True
        print(f"🔜  KICKING OFF IN NEXT HOUR ({len(upcoming)})\n")
        for m in sorted(upcoming, key=lambda x: x.get("kickoff_utc", "")):
            mins = int((datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00")) - now).total_seconds() / 60)
            print(fmt_match(m) + f"  (in {mins}m)")
        print()

    if not found:
        print("Nothing live, finished in the last hour, or starting in the next hour.")


def cmd_today():
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_matches = api_get("matches")
    matches = [m for m in all_matches if m.get("kickoff_utc", "").startswith(today_utc)]
    if not matches:
        print(f"No matches on {today_utc} UTC.")
        return
    print(f"📅  TODAY  ({today_utc} UTC)\n")
    for m in sorted(matches, key=lambda x: x.get("kickoff_utc", "")):
        print(fmt_match(m))


def cmd_results(n: int = 10):
    matches = api_get("matches", {"status": "completed"})
    recent = sorted(matches, key=lambda x: x.get("kickoff_utc", ""), reverse=True)[:n]
    if not recent:
        print("No completed matches yet.")
        return
    print(f"📋  LATEST RESULTS (last {len(recent)})\n")
    for m in recent:
        print(fmt_match(m))


def cmd_standings(group: str):
    g = group.upper()
    matches = api_get("matches", {"group": g, "status": "completed"})

    # Gather all teams in the group (including those with 0 played)
    group_teams: dict[str, dict] = {}
    all_in_group = api_get("matches", {"group": g})
    for m in all_in_group:
        for key in ("home_team", "away_team"):
            name = m[key]
            if name not in group_teams:
                group_teams[name] = dict(P=0, W=0, D=0, L=0, GF=0, GA=0)

    for m in matches:
        h, a = m["home_team"], m["away_team"]
        hs, as_ = m["home_score"], m["away_score"]
        if hs is None:
            continue
        group_teams[h]["P"] += 1
        group_teams[a]["P"] += 1
        group_teams[h]["GF"] += hs; group_teams[h]["GA"] += as_
        group_teams[a]["GF"] += as_; group_teams[a]["GA"] += hs
        if hs > as_:
            group_teams[h]["W"] += 1; group_teams[a]["L"] += 1
        elif as_ > hs:
            group_teams[a]["W"] += 1; group_teams[h]["L"] += 1
        else:
            group_teams[h]["D"] += 1; group_teams[a]["D"] += 1

    if not group_teams:
        print(f"No data for Group {g}.")
        return

    rows = []
    for name, s in group_teams.items():
        pts = s["W"] * 3 + s["D"]
        gd = s["GF"] - s["GA"]
        rows.append((name, s["P"], s["W"], s["D"], s["L"], s["GF"], s["GA"], gd, pts))
    rows.sort(key=lambda r: (-r[8], -r[7], -r[5]))

    print(f"📊  GROUP {g} STANDINGS\n")
    print(f"  {'Team':<26} {'P':>2} {'W':>2} {'D':>2} {'L':>2} {'GF':>3} {'GA':>3} {'GD':>4} {'Pts':>4}")
    print("  " + "-" * 56)
    for name, P, W, D, L, GF, GA, GD, pts in rows:
        print(f"  {name:<26} {P:>2} {W:>2} {D:>2} {L:>2} {GF:>3} {GA:>3} {GD:>+4} {pts:>4}")


def cmd_team(team: str):
    matches = api_get("matches", {"team": team.upper()})
    if not matches:
        print(f"No matches found for '{team}'.")
        return
    print(f"🏳️  {team.upper()} MATCHES ({len(matches)})\n")
    for m in sorted(matches, key=lambda x: x.get("kickoff_utc", "")):
        print(fmt_match(m, show_group=True))


def main():
    parser = argparse.ArgumentParser(description="WC 2026 scores – wc2026api.com")
    parser.add_argument("--live", action="store_true", help="Live matches (default)")
    parser.add_argument("--check", action="store_true", help="Results last hour + kickoffs next hour")
    parser.add_argument("--today", action="store_true", help="Today's matches")
    parser.add_argument("--results", action="store_true", help="Latest completed matches")
    parser.add_argument("--standings", metavar="GROUP", help="Group standings, e.g. A")
    parser.add_argument("--team", metavar="CODE", help="All matches for a team, e.g. MEX")
    args = parser.parse_args()

    if args.standings:
        cmd_standings(args.standings)
    elif args.team:
        cmd_team(args.team)
    elif args.check:
        cmd_check()
    elif args.today:
        cmd_today()
    elif args.results:
        cmd_results()
    else:
        cmd_live()


if __name__ == "__main__":
    main()
