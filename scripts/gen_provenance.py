#!/usr/bin/env python3
"""Generate public/data/data-provenance.json — the data-status disclosure table + maintenance
ledger for the dementia-exposome map (/projects/dementia-exposome).

Per data type (tab) it records, for every country, the national + sub-national source, year and
status. Countries fall through to a global `default` source unless an `overrides[ISO][type]` entry
replaces it. Overrides come from three places:
  1. 27 map countries — hand-authored aging + prevalence below (from data-sourcing-plan.md).
  2. ~146 other countries — scripts/provenance-audit.json, the all-country WebSearch source audit
     (the "徹底清查所有國家" pass). Only aspects with a real distinct source are kept; `status:"none"`
     aspects are dropped (the type default already renders "—"), and free-text `note` fields are the
     ledger annotation and are stripped from the shipped JSON to keep the client asset lean.
  3. 27 map countries — per-factor + composite PAF pulled straight from exposome.json.

Run:  python3 scripts/gen_provenance.py
"""
import json
import os
import html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public/data/data-provenance.json")
AUDIT_IN = os.path.join(ROOT, "scripts/provenance-audit.json")

# ---- country universe: ISO alpha-2 from world-globe.geojson (all countries w/ data) ----
wg = json.load(open(os.path.join(ROOT, "public/data/geo/world-globe.geojson"), encoding="utf-8"))
iso = sorted({f["properties"]["iso_a2"] for f in wg["features"] if f["properties"].get("iso_a2")})

# ---- data types (tabs) with global-default source per type ----
WB = "https://data.worldbank.org/indicator/SP.POP.65UP.TO.ZS"
GBD = "https://vizhub.healthdata.org/gbd-results/"
ACAG = "https://sites.wustl.edu/acag/"
NCD = "https://ncdrisc.org/"
GHO = "https://www.who.int/data/gho"
LANCET = "https://doi.org/10.1016/S0140-6736(24)01296-0"
types = [
    {"key": "aging", "zh": "老年人口 65+", "en": "Population 65+",
     "default": {"natSrc": "World Bank 2025 (SP.POP.65UP.TO.ZS)", "natUrl": WB, "natYear": "2025", "status": "live"}},
    {"key": "prevalence", "zh": "失智盛行率", "en": "Dementia prevalence",
     "default": {"natSrc": "GBD 2023 (IHME) 60+", "natUrl": GBD, "natYear": "2023", "status": "live"}},
    {"key": "mci", "zh": "MCI 盛行率", "en": "MCI prevalence", "planned": True,
     "default": {"natSrc": "—", "natYear": "—", "status": "none"}},
    {"key": "scd", "zh": "SCD 盛行率", "en": "SCD prevalence", "planned": True,
     "default": {"natSrc": "—", "natYear": "—", "status": "none"}},
    {"key": "pm25", "zh": "PM2.5 空污", "en": "PM2.5",
     "default": {"natSrc": "ACAG V6.GL.03", "natUrl": ACAG, "natYear": "2024", "status": "live",
                 "subSrc": "ACAG zonal admin-1", "subUrl": ACAG, "subYear": "2022–24"}},
    {"key": "hypertension", "zh": "高血壓", "en": "Hypertension",
     "default": {"natSrc": "NCD-RisC raised BP (adult, age-std)", "natUrl": NCD, "natYear": "2015", "status": "identified"}},
    {"key": "diabetes", "zh": "糖尿病", "en": "Diabetes",
     "default": {"natSrc": "NCD-RisC diabetes (adult, age-std)", "natUrl": NCD, "natYear": "2022", "status": "identified"}},
    {"key": "obesity", "zh": "肥胖", "en": "Obesity",
     "default": {"natSrc": "NCD-RisC BMI≥30 (adult, age-std)", "natUrl": NCD, "natYear": "2024", "status": "identified"}},
    {"key": "smoking", "zh": "吸菸", "en": "Smoking",
     "default": {"natSrc": "WHO GHO current tobacco (age-std)", "natUrl": GHO, "natYear": "2025", "status": "identified"}},
    {"key": "physical_inactivity", "zh": "身體活動不足", "en": "Physical inactivity",
     "default": {"natSrc": "WHO GHO insufficient activity", "natUrl": GHO, "natYear": "2022", "status": "identified"}},
    {"key": "paf", "zh": "可調控風險 PAF（合計）", "en": "Composite PAF",
     "default": {"natSrc": "5 因子 × Livingston 2024 RRs", "natUrl": LANCET, "natYear": "2015–2025", "status": "identified"}},
]

# ---- hand-authored aging + prevalence overrides for the 27 map countries (from data-sourcing-plan.md) ----
# a(): aging, p(): prevalence helpers
def a(src, url, yr, ssrc=None, surl=None, syr=None, st="seed"):
    o = {"natSrc": src, "natUrl": url, "natYear": yr, "status": st}
    if ssrc:
        o.update({"subSrc": ssrc, "subUrl": surl, "subYear": syr})
    return o


def p(src, url, yr, ssrc=None, surl=None, syr=None, st="live"):
    o = {"natSrc": src, "natUrl": url, "natYear": yr, "status": st}
    if ssrc:
        o.update({"subSrc": ssrc, "subUrl": surl, "subYear": syr})
    return o


OV = {
 "TW": {"aging": a("內政部/DGBAS", "https://eng.stat.gov.tw/", "2025", "戶政司 data.gov.tw", "https://data.gov.tw/dataset/77132", "2026"),
        "prevalence": p("NHRI 全國調查", "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12726225/", "2019–2023")},
 "JP": {"aging": a("e-Stat", "https://www.e-stat.go.jp/en/", "2024", "e-Stat 都道府県", "https://www.e-stat.go.jp/en/", "2024"),
        "prevalence": p("Hisayama/Ninomiya", "https://doi.org/10.1186/s13195-025-01909-1", "2022", "Toyama 都道府県推估", "https://doi.org/10.1186/s12877-021-02540-z", "2020–45")},
 "KR": {"aging": a("KOSIS / World Bank", "https://kosis.kr/eng/", "2025", "KOSIS 시도", "https://kosis.kr/eng/", "2024"),
        "prevalence": p("NaSDEK / 중앙치매센터", "https://www.nid.or.kr", "2023", "중앙치매센터 시군구별 치매현황（2023 역학조사율）", "https://www.data.go.kr/data/15073342/fileData.do", "2025")},
 "CN": {"aging": a("國家統計局 7 普", "https://www.stats.gov.cn/english/", "2020", "NBS 省級", "https://www.stats.gov.cn/english/", "2020"),
        "prevalence": p("Jia 2020", "https://www.thelancet.com/journals/lanpub/article/PIIS2468-2667(20)30185-7/fulltext", "2015–18", "Liu-Gao 2024（28 省）", "https://pmc.ncbi.nlm.nih.gov/articles/PMC11225804/", "2018")},
 "TH": {"aging": a("NSO（2025 普查）", "https://www.nso.go.th/nsoweb/index?set_lang=en", "2025", "NSO 府級", "https://www.nso.go.th/nsoweb/index?set_lang=en", "2025"),
        "prevalence": p("Jitapunkul 2001 / GBD", "https://pubmed.ncbi.nlm.nih.gov/11460954/", "2001", st="identified")},
 "VN": {"aging": a("GSO（2019 普查）", "https://www.gso.gov.vn/en/", "2019", "GSO 省級", "https://dashboard.gso.gov.vn/", "2019"),
        "prevalence": p("Nguyen 2019", "https://pmc.ncbi.nlm.nih.gov/articles/PMC6706920/", "2019", st="identified")},
 "ID": {"aging": a("BPS（2020 普查）", "https://www.bps.go.id/en", "2020", "BPS 省/縣", "https://www.bps.go.id/en", "2020"),
        "prevalence": p("STRiDE 2022 / GBD 省級", "https://pmc.ncbi.nlm.nih.gov/articles/PMC10305093/", "2022")},
 "PH": {"aging": a("PSA OpenSTAT", "https://openstat.psa.gov.ph/", "2020", "PSA 區/省", "https://openstat.psa.gov.ph/", "2020"),
        "prevalence": p("Dominguez 2018 / GBD 區", "https://journals.sagepub.com/doi/abs/10.3233/JAD-180095", "2018")},
 "MY": {"aging": a("DOSM OpenDOSM", "https://open.dosm.gov.my/data-catalogue/population_state", "2025", "OpenDOSM 州/縣", "https://open.dosm.gov.my/data-catalogue/population_district", "2025", st="live"),
        "prevalence": p("NHMS 2025（州）", "https://iku.gov.my/", "2025", "NHMS 各州", "https://iku.gov.my/", "2025")},
 "MM": {"aging": a("2014 普查 / MMSIS", "https://mmsis.gov.mm/", "2014", "MMSIS 省/邦", "https://mmsis.gov.mm/", "2014"),
        "prevalence": p("Aung 2020 / GBD", "https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0236656", "2020", st="identified")},
 "IN": {"aging": a("Census 2011 + MoSPI 推估", "https://www.mospi.gov.in/4-population", "2011", "NFHS-5 縣區", "https://rchiips.org/nfhs/", "2019–21"),
        "prevalence": p("LASI-DAD（Lee 2023）", "https://doi.org/10.1002/alz.12928", "2018–20", "LASI-DAD 各邦", "https://doi.org/10.1002/alz.12928", "2018–20")},
 "PK": {"aging": a("7th Census 2023", "https://www.pbs.gov.pk/", "2023", "PBS 縣/tehsil", "https://www.pbs.gov.pk/", "2023"),
        "prevalence": p("GBD 省級 / 地方研究", GBD, "2023", st="identified")},
 "BD": {"aging": a("Census 2022（BBS）", "https://www.bbs.gov.bd/", "2022", "BBS 縣/區", "https://www.bbs.gov.bd/", "2022"),
        "prevalence": p("Lancet-SEA 2023", "https://www.thelancet.com/journals/lansea/article/PIIS2772-3682(23)00117-8/fulltext", "2023")},
 "GB": {"aging": a("ONS / Nomis", "https://www.nomisweb.co.uk/datasets/pestsyoala", "2024", "Nomis LAD", "https://www.nomisweb.co.uk/datasets/pestsyoala", "2024", st="live"),
        "prevalence": p("OHID Fingertips + CFAS II", "https://fingertips.phe.org.uk/profile-group/mental-health/profile/dementia", "2023/24", "Fingertips LAD/ICB", "https://fingertips.phe.org.uk/", "2023/24")},
 "DE": {"aging": a("Destatis GENESIS", "https://www.destatis.de/EN/", "2024", "GENESIS Kreis", "https://www.destatis.de/EN/", "2024", st="live"),
        "prevalence": p("Thyrian 2020（Kreis）", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7606288/", "2020", "Thyrian Kreis 地圖", "https://pmc.ncbi.nlm.nih.gov/articles/PMC7606288/", "2020")},
 "FR": {"aging": a("INSEE", "https://www.insee.fr/", "2024", "INSEE commune/dépt", "https://api.gouv.fr/les-api/api_donnees_locales", "2024", st="live"),
        "prevalence": p("Assurance Maladie 製圖", "https://www.assurance-maladie.ameli.fr/etudes-et-donnees/cartographie-prevalence-demences-maladie-alzheimer", "2022", "Ameli 各省", "https://www.assurance-maladie.ameli.fr/", "2022")},
 "IT": {"aging": a("ISTAT", "https://esploradati.istat.it/databrowser/", "2025", "ISTAT provincia", "https://demo.istat.it/", "2025", st="live"),
        "prevalence": p("ISS / Bacigalupo 2018", "https://journals.sagepub.com/doi/10.3233/JAD-180416", "2018", st="identified")},
 "ES": {"aging": a("INE Tempus3", "https://www.ine.es/", "2025", "INE provincia", "https://www.ine.es/", "2025", st="live"),
        "prevalence": p("Alzheimer Europe 2025 / Madrid", "https://pubmed.ncbi.nlm.nih.gov/25444413/", "2015–25", st="identified")},
 "PL": {"aging": a("GUS BDL", "https://api.stat.gov.pl/Home/BdlApi?lang=en", "2024", "BDL powiat/woj", "https://bdl.stat.gov.pl/", "2024", st="live"),
        "prevalence": p("Alzheimer Europe 2025 / NFZ", "https://ezdrowie.gov.pl/", "2024–25", st="identified")},
 "US": {"aging": a("Census ACS/PEP", "https://data.census.gov/", "2023", "ACS tract", "https://www.census.gov/data/developers.html", "2019–23", st="live"),
        "prevalence": p("Alzheimer's Assoc. F&F", "https://www.alz.org/alzheimers-dementia/facts-figures", "2025", "各州（Alz Assoc）", "https://www.alz.org/alzheimers-dementia/facts-figures", "2025")},
 "CA": {"aging": a("StatCan", "https://www.statcan.gc.ca/en/developers", "2024", "StatCan DA/CSD", "https://www.statcan.gc.ca/en/developers", "2021–24", st="live"),
        "prevalence": p("CCDSS（PHAC）", "https://health-infobase.canada.ca/ccdss/", "2023–24", "各省/衛生區", "https://health-infobase.canada.ca/ccdss/", "2023–24")},
 "BR": {"aging": a("IBGE SIDRA（2022 普查）", "https://sidra.ibge.gov.br/", "2022", "IBGE 州/市", "https://sidra.ibge.gov.br/", "2022", st="live"),
        "prevalence": p("ELSI-Brasil", "https://academic.oup.com/biomedgerontology/article/78/6/1060/6995432", "2019", st="identified")},
 "MX": {"aging": a("INEGI（2020 普查）+ CONAPO", "https://en.www.inegi.org.mx/", "2020", "INEGI municipio", "https://en.www.inegi.org.mx/", "2020", st="live"),
        "prevalence": p("ENASEM/MHAS", "https://pmc.ncbi.nlm.nih.gov/articles/PMC3557523/", "2011", st="identified")},
 "AU": {"aging": a("ABS", "https://api.data.abs.gov.au/", "2024", "ABS SA2/LGA", "https://api.data.abs.gov.au/", "2024", st="live"),
        "prevalence": p("AIHW", "https://www.aihw.gov.au/reports/dementia/dementia-in-aus/contents/summary", "2024", "各州（AIHW）", "https://www.aihw.gov.au/", "2024")},
 "NZ": {"aging": a("Stats NZ", "https://www.stats.govt.nz/tools/aotearoa-data-explorer/", "2025", "Stats NZ TA", "https://www.stats.govt.nz/", "2025", st="live"),
        "prevalence": p("capture-recapture 2024", "https://onlinelibrary.wiley.com/doi/10.1002/gps.6131", "2021", st="identified")},
 "TR": {"aging": a("TÜİK ABPRS", "https://data.tuik.gov.tr/", "2024", "TÜİK 81 省", "https://data.tuik.gov.tr/", "2024", st="live"),
        "prevalence": p("2025 全國 AD 研究 / EHR", "https://link.springer.com/article/10.1007/s44197-025-00435-5", "2024–25", st="identified")},
 "IR": {"aging": a("SCI Census 2016", "https://www.amar.org.ir/english", "2016", "SCI 31 省", "https://www.amar.org.ir/english", "2016"),
        "prevalence": p("2024 統合分析 / GBD 31 省", "https://bmcpublichealth.biomedcentral.com/articles/10.1186/s12889-024-18415-y", "2024", st="identified")},
}

# ---- all-country source audit (scripts/provenance-audit.json, ~146 non-map countries) ----
# Keep only aspects with a real distinct source; drop status=="none" (default renders "—") + notes.
ASPECTS = ("aging", "prevalence", "mci", "scd")
audit = json.load(open(AUDIT_IN, encoding="utf-8"))
for ISO, aspects in audit.items():
    for k in ASPECTS:
        v = aspects.get(k)
        if not isinstance(v, dict):
            continue
        if v.get("status") == "none" or not v.get("natSrc"):
            continue
        cell = {kk: vv for kk, vv in v.items() if kk != "note"}
        OV.setdefault(ISO, {})[k] = cell

# ---- factor + composite overrides straight from exposome.json (~200 countries, all "live" in the tool) ----
# exposome.json carries factor `source`+`year` but no URL, so map the source label → its canonical link
# (keeps every factor cell hyperlinked, matching the audited rows rather than rendering as bare text).
NHIS_TW = "https://nhis.nhri.edu.tw/"


def _factor_url(src):
    s = src or ""
    if "NCD-RisC" in s:
        return NCD
    if "GHO" in s:
        return GHO
    if "NHIS" in s and "Taiwan" in s:
        return NHIS_TW
    return None


expo = json.load(open(os.path.join(ROOT, "public/data/exposome/exposome.json"), encoding="utf-8"))["countries"]
FKEYS = ["hypertension", "diabetes", "obesity", "smoking", "physical_inactivity"]
for cc, e in expo.items():
    ISO = cc.upper()
    OV.setdefault(ISO, {})
    yrs = []
    for fk in FKEYS:
        f = e.get("factors", {}).get(fk)
        if not f:
            continue
        src = f.get("source", "—")
        cell = {"natSrc": src, "natYear": str(f.get("year", "")), "status": "live"}
        url = _factor_url(src)
        if url:
            cell["natUrl"] = url
        OV[ISO][fk] = cell
        if f.get("year"):
            yrs.append(int(f["year"]))
    if e.get("composite_paf_pct") is not None:
        yr = f"{min(yrs)}–{max(yrs)}" if yrs else ""
        OV[ISO]["paf"] = {"natSrc": f"{e.get('n_factors', len(FKEYS))} 因子 × Livingston 2024 RRs", "natUrl": LANCET, "natYear": yr, "status": "live"}

# ---- MCI/SCD deep audit (scripts/provenance-audit-cognitive.json) — authority for mci/scd, all countries ----
# A dedicated MCI + SCD WebSearch pass (incl. the 27 map countries the first audit skipped). Runs last so it
# is the single source of truth for those two aspects, overriding the scattered mci/scd from pass 1.
COG_IN = os.path.join(ROOT, "scripts/provenance-audit-cognitive.json")
cog = json.load(open(COG_IN, encoding="utf-8"))
for ISO, aspects in cog.items():
    for k in ("mci", "scd"):
        v = aspects.get(k)
        if not isinstance(v, dict):
            continue
        if v.get("status") == "none" or not v.get("natSrc"):
            continue
        OV.setdefault(ISO, {})[k] = {kk: vv for kk, vv in v.items() if kk != "note"}

# ---- MCI: the few strong-data countries actually wired into the map (mci-national.json) → status "live" ----
# (runs after the cognitive audit so it wins for the countries whose MCI is now on the globe)
_mci = json.load(open(os.path.join(ROOT, "public/data/mci/mci-national.json"), encoding="utf-8"))["countries"]
for _cc, _m in _mci.items():
    _st = "live" if _m.get("kind") == "national" else "seed"   # national study = live; Bai-2022 regional baseline = seed
    OV.setdefault(_cc.upper(), {})["mci"] = {"natSrc": _m["src"], "natUrl": _m["url"], "natYear": str(_m["year"]), "status": _st}

# ---- SCD: countries with a national self-report value (scd-national.json) → status "seed" (heterogeneous) ----
_scd_path = os.path.join(ROOT, "public/data/scd/scd-national.json")
if os.path.exists(_scd_path):
    for _cc, _s in json.load(open(_scd_path, encoding="utf-8"))["countries"].items():
        OV.setdefault(_cc.upper(), {})["scd"] = {"natSrc": _s["src"], "natUrl": _s["url"], "natYear": str(_s["year"]), "status": "seed"}

# ---- final clean: unescape any stray HTML entities in every override string ----
SRC_CANON = {   # same underlying source must read identically map-vs-nonmap countries
    "ACAG zonal admin-1（27 國）": "ACAG zonal admin-1",
    "WHO GHO tobacco": "WHO GHO current tobacco (age-std)",
    "NCD-RisC raised BP": "NCD-RisC raised BP (adult, age-std)",
    "NCD-RisC diabetes": "NCD-RisC diabetes (adult, age-std)",
    "NCD-RisC diabetes 18+ (age-std)": "NCD-RisC diabetes (adult, age-std)",
    "NCD-RisC BMI≥30": "NCD-RisC BMI≥30 (adult, age-std)",
    "5 因子合計 × Livingston 2024": "5 因子 × Livingston 2024 RRs",
}
for ISO in OV:
    for asp, cell in OV[ISO].items():
        cell = {k: (html.unescape(v) if isinstance(v, str) else v) for k, v in cell.items()}
        for _f in ("natSrc", "subSrc"):
            if cell.get(_f) in SRC_CANON:
                cell[_f] = SRC_CANON[cell[_f]]
        OV[ISO][asp] = cell

doc = {
    "meta": {"note": "Data-provenance / completeness ledger for the dementia-exposome map. "
                     "Per data type: national + sub-national source, year, and status. "
                     "status: live=in the tool · seed=partial estimate · identified=source found, not yet wired · none=no data yet. "
                     "27 map countries hand-authored; ~146 other countries from an all-country WebSearch source audit "
                     "(scripts/provenance-audit.json); MCI/SCD from a dedicated cognitive-impairment audit "
                     "(scripts/provenance-audit-cognitive.json). See docs/side-projects/dementia-exposome/"
                     "all-country-source-audit.md — modelled/latest-available estimates.",
             "built": "2026-07-15"},
    "types": types,
    "iso": iso,
    "overrides": OV,
}
with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
print("wrote", OUT, "· countries", len(iso), "· overrides", len(OV),
      "· size", os.path.getsize(OUT) // 1024, "KB")
