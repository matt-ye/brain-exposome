#!/usr/bin/env python3
"""Literature radar for the brain-exposome map.

For each watch-topic in ``scripts/literature-watch.json`` it queries the
**Europe PMC** REST API (free, no key; indexes PubMed + PMC + preprints) for
papers published in the last ``days_window`` days, and lists them as *candidates*
for a human to judge — never auto-ingests. TITLE-scoped queries keep the noise
down (a naive "dementia prevalence" search returns hundreds/month; the tuned
title queries return a handful).

Optional ``--triage``: if any LLM provider key is set (see scripts/llm_triage.py —
NVIDIA NIM → Groq → Cloudflare fallback, ported from the dreamcatcher ai-worker),
each candidate is scored keep/drop and the drops are collapsed. Fail-open: with no
keys, or on any error, everything is kept (identical to the keyless behaviour).

Stdlib only. Robust: a topic whose query fails is reported as "could not check".

De-duplication: a paper is listed once per run even if several topics match it,
and — with ``--seen-from-issues`` (CI) or ``--seen-file`` (local) — a paper already
listed in an earlier month is skipped. The 45-day window overlaps the monthly run,
so without this the same paper shows up twice. The previous issues ARE the record
of what was reported, so CI reads them back instead of keeping a state file (an
Actions cache is evicted after 7 idle days — a monthly job would never hit it).
Fail-open: if the history cannot be read, nothing is skipped and the report says so.

Usage:
  python scripts/literature_watch.py --out report.md --append           # keyless
  python scripts/literature_watch.py --out report.md --append --triage   # + LLM filter
  python scripts/literature_watch.py --config scripts/literature-watch.ot.json \\
      --seen-file seen.json --triage --out report.md                     # another topic set, local
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import llm_triage
import triage_rules

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(HERE, "literature-watch.json")
# Europe PMC asks for a contact in the UA. Read it from the environment so no
# personal address lives in a public repo; fall back to the project URL.
_CONTACT = os.environ.get("LITWATCH_CONTACT", "").strip()
UA = {"User-Agent": "brain-exposome-litwatch/1.2 "
      + (f"(mailto:{_CONTACT})" if _CONTACT else "(+https://github.com/matt-ye/brain-exposome)")}
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
GH_API = "https://api.github.com"
TIMEOUT = 30
LINK_RE = re.compile(r"https://(?:doi\.org/[^\s)\]>]+|europepmc\.org/article/[^\s)\]>]+)")


def epmc_search(query, since_iso, until_iso, page_size):
    full = f"({query}) AND (FIRST_PDATE:[{since_iso} TO {until_iso}])"
    params = urllib.parse.urlencode({
        "query": full, "format": "json", "pageSize": str(page_size),
        "sort": "P_PDATE_D desc", "resultType": "core",   # core → includes abstractText for triage
    })
    req = urllib.request.Request(f"{EPMC}?{params}", headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return d.get("hitCount", 0), d.get("resultList", {}).get("result", [])


def journal_of(rec):
    return (rec.get("journalTitle")
            or (rec.get("journalInfo") or {}).get("journal", {}).get("title")
            or (rec.get("bookOrReportDetails") or {}).get("publisher")
            or "preprint/other")


def link_for(rec):
    if rec.get("doi"):
        return f"https://doi.org/{rec['doi']}"
    src, rid = rec.get("source"), rec.get("id")
    return f"https://europepmc.org/article/{src}/{rid}" if src and rid else ""


def _title_key(title):
    return "title:" + re.sub(r"\W+", " ", (title or "").lower()).strip()


def paper_keys(rec):
    """Keys that identify one paper, so the same work found twice is listed once.

    Two keys, because one is not enough: Europe PMC returns the MEDLINE record (with
    a DOI) and the PMC record (often without) as separate hits for the same article —
    seen in the first OT run, where two papers each appeared twice. The link catches
    re-runs; the normalised title catches the MED/PMC pair. Both are what the report
    prints, so a past report can be read back with a regex (see keys_in_text)."""
    keys = {_title_key(rec.get("title"))}
    url = link_for(rec)
    if url:
        keys.add(url.lower().rstrip(".,;"))
    return keys


MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")


def keys_in_text(text):
    """Paper keys (links and titles) in an earlier report — the inverse of line_for."""
    keys = {m.lower().rstrip(".,;") for m in LINK_RE.findall(text or "")}
    keys |= {_title_key(t) for t, _ in MD_LINK_RE.findall(text or "")}
    return keys


def seen_from_issues(label):
    """Paper keys already listed in this repo's earlier issues with `label`.

    Returns (keys, error). Needs GITHUB_REPOSITORY + GH_TOKEN/GITHUB_TOKEN, which
    Actions provides; anything else is an error and the caller fails open."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return set(), "GITHUB_REPOSITORY / GH_TOKEN not set"
    keys = set()
    try:
        for page in range(1, 6):                       # ≤500 issues is years of monthly runs
            q = urllib.parse.urlencode({"labels": label, "state": "all", "per_page": 100, "page": page})
            req = urllib.request.Request(f"{GH_API}/repos/{repo}/issues?{q}", headers={
                **UA, "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                batch = json.loads(r.read().decode("utf-8", "replace"))
            for issue in batch:
                keys |= keys_in_text(issue.get("body"))
            if len(batch) < 100:
                break
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        return set(), type(e).__name__
    return keys, None


def line_for(rec, verdict=None):
    title = (rec.get("title") or "").rstrip(".")
    url = link_for(rec)
    cite = f"[{title}]({url})" if url else title
    tag = f" — _{verdict['reason']}_" if verdict and verdict.get("reason") else ""
    return f"- {rec.get('firstPublicationDate', '?')} · *{journal_of(rec)}* — {cite}{tag}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--triage", action="store_true", help="LLM keep/drop filter if provider keys are set")
    ap.add_argument("--config", default=CONFIG, help="topic set (default: literature-watch.json)")
    ap.add_argument("--seen-from-issues", metavar="LABEL", default="",
                    help="skip papers already listed in earlier issues with this label (CI)")
    ap.add_argument("--seen-file", default="",
                    help="JSON list of already-reported paper keys; read, then updated (local runs)")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    config_name = os.path.basename(args.config)
    window = int(cfg.get("days_window", 45))
    per_topic = int(cfg.get("max_per_topic", 6))
    until = datetime.date.today()
    since = until - datetime.timedelta(days=window)
    since_iso, until_iso = since.isoformat(), until.isoformat()

    if cfg.get("rubric"):                 # a topic set may judge relevance by its own rules
        llm_triage.RUBRIC = cfg["rubric"]
    do_triage = args.triage and llm_triage.available()
    # Ledger mode: judge each paper against what the map uses now (see triage_rules.py).
    # Fail-open like everything else here: no readable ledger → the old keep/drop rubric.
    ledger, ledger_note = None, ""
    if do_triage and cfg.get("ledger"):
        try:
            ledger = triage_rules.load_ledger(os.path.join(ROOT, cfg["ledger"]))
        except (OSError, ValueError, KeyError) as e:
            ledger_note = f"⚠️ ledger `{cfg['ledger']}` unreadable ({type(e).__name__}) — fell back to the plain rubric"

    seen, seen_note = set(), ""
    if args.seen_from_issues:
        seen, err = seen_from_issues(args.seen_from_issues)
        seen_note = (f"⚠️ could not read earlier issues ({err}) — papers from previous months may repeat"
                     if err else f"{len(seen)} paper(s) from earlier `{args.seen_from_issues}` issues")
    if args.seen_file and os.path.exists(args.seen_file):
        try:
            seen |= set(json.load(open(args.seen_file, encoding="utf-8")))
        except ValueError:
            seen_note = f"⚠️ {args.seen_file} unreadable — papers from previous runs may repeat"
    listed = set()                        # keys printed this run (also dedups across topics)
    skipped_seen = skipped_dup = 0

    L = [f"## 📚 New literature — last {window} days ({since_iso} → {until_iso})\n"]
    L.append(cfg.get("intro") or
             "Candidate papers for the figures the map uses (Europe PMC, TITLE-scoped). "
             "**For review, not auto-ingested** — if one updates a number we use, follow "
             "extract→review→register.")
    # Filled in after the loop — the header has to name the model that actually
    # answered, and that is not known until the first verdict comes back. Claiming
    # "LLM-triaged" when every tier silently failed is the failure mode worth
    # designing against: the run still succeeds and filters nothing.
    triage_line = len(L)
    L.append("")
    triaged_by = set()
    triage_tried = 0      # a quiet month has nothing to score — not the same as a broken chain

    total_keep = 0
    for t in cfg["topics"]:
        label = t["label"]
        try:
            hits, results = epmc_search(t["query"], since_iso, until_iso, per_topic)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
            L.append(f"### {label}\n- ⚠️ could not check ({type(e).__name__})\n")
            continue

        keeps, drops = [], []
        for r in results:
            ks = paper_keys(r)
            if ks & seen:
                skipped_seen += 1
                continue
            if ks & listed:
                skipped_dup += 1
                continue
            listed |= ks
            v = None
            if do_triage:
                triage_tried += 1
                if ledger:
                    facts = llm_triage.extract(r.get("title", ""), r.get("abstractText", ""), hint=label)
                    if facts:
                        bucket, why = triage_rules.decide(facts, ledger)
                        v = {"verdict": "keep" if bucket == triage_rules.UPDATE else "drop",
                             "reason": why, "bucket": bucket,
                             "tier": facts.get("tier"), "model": facts.get("model")}
                else:
                    v = llm_triage.triage(r.get("title", ""), r.get("abstractText", ""))
                if v:
                    triaged_by.add(f"{v.get('tier', '?')}/`{v.get('model', '?')}`")
                time.sleep(1)                      # gentle pacing for free-tier TPM
            if v and v.get("verdict") == "drop":
                drops.append(line_for(r, v))
            else:
                keeps.append(line_for(r, v))
        total_keep += len(keeps)

        if keeps:
            L.append(f"### {label} — {hits} new" + (f", {len(keeps)} kept" if do_triage else ""))
            L.extend(keeps)
            if drops:
                L.append(f"\n<details><summary>{len(drops)} filtered out by triage</summary>\n")
                L.extend(drops)
                L.append("\n</details>")
            L.append("")
        elif drops:
            L.append(f"### {label} — {hits} new, 0 kept")
            L.append(f"<details><summary>{len(drops)} filtered out by triage</summary>\n")
            L.extend(drops)
            L.append("\n</details>\n")
        else:
            L.append(f"### {label}\n- none in the last {window} days\n")

        if t.get("manual_also"):
            L.append(f"  <sub>↳ {t.get('note', '')}</sub>")
            for m in t["manual_also"]:
                L.append(f"  - 🔎 manual: {m}")
            L.append("")

    if not do_triage:
        L[triage_line] = "_Keyless: all candidates listed (set provider secrets + `--triage` to filter)._\n"
    elif triaged_by and ledger:
        L[triage_line] = (f"_🤖 Facts read by {', '.join(sorted(triaged_by))}, then compared with "
                          f"`{cfg['ledger']}` (what the map uses now, per country and layer). Kept = newer "
                          "data or a gap; older / same-data / non-estimate papers are collapsed with the "
                          "comparison that ruled them out._\n")
    elif triaged_by:
        L[triage_line] = f"_🤖 LLM-triaged by {', '.join(sorted(triaged_by))}; drops are collapsed._\n"
    elif not triage_tried:
        L[triage_line] = "_🤖 Triage is on, but there was nothing to score this month._\n"
    else:
        L[triage_line] = ("_⚠️ **Triage was requested but every provider failed** — nothing was "
                          "filtered and every candidate below is listed. Check the provider keys "
                          "and `scripts/llm_triage.py`._\n")

    dedup = []
    if skipped_seen:
        dedup.append(f"{skipped_seen} already reported in an earlier run")
    if skipped_dup:
        dedup.append(f"{skipped_dup} duplicate record(s) (same paper twice in Europe PMC, or in several topics)")
    if ledger_note:
        L.append(f"_{ledger_note}._\n")
    if seen_note.startswith("⚠️"):
        L.append(f"_{seen_note}._\n")
    elif dedup:
        L.append(f"_Skipped: {'; '.join(dedup)}._\n")

    L.append(f"> {total_keep} candidate(s) to review across {len(cfg['topics'])} topics. "
             f"Queries live in `scripts/{config_name}`.")

    if args.seen_file:
        tmp = args.seen_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(seen | listed), f, ensure_ascii=False, indent=0)
        os.replace(tmp, args.seen_file)

    report = "\n".join(L)
    if args.out:
        with open(args.out, "a" if args.append else "w", encoding="utf-8") as f:
            if args.append:
                f.write("\n\n---\n\n")
            f.write(report)
    else:
        sys.stdout.buffer.write(report.encode("utf-8"))
    print(f"\n[litwatch] {total_keep} kept across {len(cfg['topics'])} topics "
          f"({since_iso}→{until_iso}) triage={'on' if do_triage else 'off'} "
          f"skipped_seen={skipped_seen} skipped_dup={skipped_dup}", file=sys.stderr)


if __name__ == "__main__":
    main()
