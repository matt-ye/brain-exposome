#!/usr/bin/env python3
"""Decide whether a paper could update the map — by comparing it with what the map uses NOW.

The LLM used to answer "does this paper report a prevalence?" (llm_triage.RUBRIC).
That is the wrong question: in 2026-08..10 it kept a China survey from 2013–15 while
the map already used CHARLS 2018, and a Europe GBD 2023 paper while the map already
used GBD 2023 — new papers, old or identical data. The useful question is "is this
newer than the source the map uses for that country and layer, and is it the same
kind of number?" — and the map already records its sources, per country and layer,
in public/data/data-provenance.json.

So the work is split. The LLM only READS the abstract into facts (llm_triage.extract);
this module compares those facts with the ledger. Updating the ledger therefore
updates the triage, and every verdict carries the comparison that produced it.

Stdlib only; no network.
"""
import datetime
import json
import re

# Layer ids = the `types[].key` values in data-provenance.json.
LAYERS = ("prevalence", "mci", "scd", "aging", "pm25", "paf",
          "hypertension", "diabetes", "obesity", "smoking", "physical_inactivity")
# What kind of number each layer ingests. A relative risk cannot update a prevalence
# layer, and a prevalence cannot update the risk (PAF) layer.
MEASURES = {"prevalence": {"prevalence"}, "mci": {"prevalence"}, "scd": {"prevalence"},
            "aging": {"share", "projection"}, "pm25": {"exposure"},
            "paf": {"relative_risk", "paf"}}
for _rf in ("hypertension", "diabetes", "obesity", "smoking", "physical_inactivity"):
    MEASURES[_rf] = {"prevalence"}

UPDATE, OLDER, NOT_ESTIMATE = "update", "older", "not_estimate"
_STOP = {"national", "survey", "study", "data", "database", "health", "global", "the", "and",
         "of", "cohort", "estimates", "estimate", "burden", "disease", "registry", "insurance"}


def load_ledger(path):
    d = json.load(open(path, encoding="utf-8"))
    defaults = {t["key"]: t.get("default") or {} for t in d.get("types", [])}
    return {"defaults": defaults, "overrides": d.get("overrides", {})}


def end_year(text, today=None):
    """Last year a source's DATA covers: '2015–18'→2018, '2019–2023'→2023, '2022'→2022.
    A range that runs past today is a projection ('2020–45'): its data stops at the start."""
    if text is None:
        return None
    s = str(text)
    years = []
    for m in re.finditer(r"(19|20)\d\d(?:\s*[–\-/~]\s*(\d{2,4}))?", s):
        y0 = int(m.group(0)[:4])
        years.append(y0)
        if m.group(2):
            tail = m.group(2)
            years.append(int(tail) if len(tail) == 4 else (y0 // 100) * 100 + int(tail))
    if not years:
        return None
    this_year = (today or datetime.date.today()).year
    past = [y for y in years if y <= this_year]
    return max(past) if past else min(years)


def in_use(ledger, layer, iso2):
    """(source, data year) the map uses for this layer + country — sub-national entry first,
    because that is the finer figure a new national paper would have to beat."""
    o = (ledger["overrides"].get(iso2) or {}).get(layer) if iso2 else None
    d = o or ledger["defaults"].get(layer) or {}
    for src_k, yr_k in (("subSrc", "subYear"), ("natSrc", "natYear")):
        src, yr = d.get(src_k), d.get(yr_k)
        if src and src != "—" and end_year(yr):
            return src, end_year(yr)
    return None, None


def _tokens(name):
    return {w for w in re.findall(r"[a-z0-9]{3,}", (name or "").lower()) if w not in _STOP}


def same_dataset(dataset, source):
    """True when the paper's underlying dataset is the one the map already uses (e.g. both
    GBD). Token overlap on distinctive words only; a miss just falls back to the year test."""
    a, b = _tokens(dataset), _tokens(source)
    return bool(a and b and a & b)


def decide(facts, ledger, today=None):
    """facts (from llm_triage.extract):
         estimate: bool   — reports a population-level number at all
         layer: one of LAYERS or "none"
         measure: prevalence | incidence | relative_risk | paf | share | projection | exposure | other
         population: general | subgroup
         method: survey | administrative | modelled | meta_analysis | other
         representative: bool — sample stands for a whole country / region
         countries: ["KR", ...] ISO-2, or ["GLOBAL"] / [] for global or regional
         data_end_year: int | null    — last year the data were COLLECTED (not published)
         dataset: short name of the underlying survey/database ("GBD 2023", "CHARLS", "NHIS")
       returns (bucket, reason)  bucket ∈ {update, older, not_estimate}"""
    layer = facts.get("layer")
    if not facts.get("estimate") or layer not in LAYERS:
        return NOT_ESTIMATE, "no population estimate for a map layer"
    if facts.get("population") == "subgroup":
        return NOT_ESTIMATE, "subgroup, not the general population"
    measure = facts.get("measure")
    if measure and measure not in MEASURES[layer]:
        return NOT_ESTIMATE, f"{measure}, layer needs {'/'.join(sorted(MEASURES[layer]))}"
    # A local cohort cannot stand in for a country. Only an explicit `false` rules a paper out;
    # a model that leaves the field empty is not evidence either way.
    rep = facts.get("representative")
    if layer != "paf" and (rep is False or str(rep).lower() == "false"):
        return NOT_ESTIMATE, "local sample, not representative"
    if layer in ("mci", "scd") and facts.get("method") == "administrative":
        return NOT_ESTIMATE, "claims-diagnosed, not screened prevalence"
    # The PAF layer multiplies prevalence by POOLED relative risks (Livingston 2024). One
    # cohort's hazard ratio cannot replace a pooled RR — only a meta-analysis or a new
    # commission can.
    if layer == "paf" and facts.get("method") != "meta_analysis":
        return NOT_ESTIMATE, "single-study risk; layer uses pooled RRs"

    year = facts.get("data_end_year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    countries = [c for c in (facts.get("countries") or []) if isinstance(c, str) and len(c) == 2
                 and c.upper() != "GL"] or [None]          # None → the layer's global default

    best = None   # the most favourable comparison across the paper's countries
    for c in countries:
        c = c.upper() if c else None
        src, used = in_use(ledger, layer, c)
        where = c or "global"
        if src is None:
            return UPDATE, f"{where}: map has no {layer} source yet"
        # Same dataset first: a GBD-based paper with no stated year is still GBD, which the map has.
        if same_dataset(facts.get("dataset"), src) and (year is None or year <= used):
            cand = (OLDER, f"same data as {where} in use: {src} ({used})")
        elif year is None:
            cand = (UPDATE, f"data year unknown; {where} uses {src} ({used})")
        elif year > used:
            cand = (UPDATE, f"data {year} > {where} in use {src} ({used})")
        else:
            cand = (OLDER, f"data {year} ≤ {where} in use {src} ({used})")
        if best is None or (cand[0] == UPDATE and best[0] != UPDATE):
            best = cand
    return best
