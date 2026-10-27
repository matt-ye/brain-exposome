# Data download points — one-click list for the owner

*The blocked prevalence/population datasets the calculator & maps need, with exact open-immediately URLs. Prepared July 2026.*

## Why this list exists

The build sandbox reaches **ACAG PM2.5, Natural Earth, npm, GitHub** — so the
PM2.5 map layers (🇹🇼🇯🇵🇰🇷) are already real. It **cannot** reach government /
research portals (strict policy egress; `scripts/scrape.py` via Playwright is
blocked here too — it's built for *your* unrestricted network). So the two
inputs that turn PM2.5 maps into **prevalence** maps — *population by age* and
*prevalence rates* — you download once and drop into `scripts/_data_in/`, then
re-run `python3 scripts/build_data.py`.

**How to use each row:** open the **Direct page**, do the one **Export** step,
rename to the **Save as** filename, drop it in `scripts/_data_in/`. Schemas the
pipeline expects are at the bottom. `scripts/scrape.py <recipe>` automates the
click-path on your own machine (`python3 scripts/scrape.py --list`).

Priority order is top-to-bottom within each country.

---

## 🇹🇼 Taiwan

| # | Feeds | Direct page | Export | Save as |
|---|---|---|---|---|
| 1 | **Town-level (368) prevalence** — upgrades the map from county to 鄉鎮市區 | data.gov.tw/dataset/**77132** (內政部戶政司「鄉鎮市區人口按性別及單一年齡」) · API: `ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{yyymm}` (e.g. `11312`) | Download the latest month CSV (or GET the API, `page=1..N`) | `tw-town-pop.csv` |
| 2 | Exact county age bands (replaces the committed 65+ snapshot) | data.gov.tw/dataset/**33935** (縣市別 5 歲年齡組人口) | Download CSV | `tw-county-pop.csv` |
| 3 | **MCI layer** the owner asked for | NHRI 全國社區失智症流行病學調查 — projects.nhri.edu.tw/spdc + the 2024 press release (search `失智症盛行率 2024 記者會`) | Copy the **age-band MCI + dementia %** table (or save the PDF) | `nhri-mci-rates.csv` |
| 4 | Diagnosed (utilisation) prevalence to compare vs modelled | 衛福部健保署 NHIRD / 健康資料加值 (application required) | — (restricted) | — |
| 5 | Modifiable-factor burden map (HTN, DM, hearing, smoking by area) | 國健署 HPA 健康促進統計 / 國民健康訪問調查 (NHIS) — hpa.gov.tw open data | Download the county/district CSVs | `tw-risk-factors.csv` |

## 🇯🇵 Japan

| # | Feeds | Direct page | Export | Save as |
|---|---|---|---|---|
| 1 | **Prefecture (47) prevalence** — population half | e-Stat **e-stat.go.jp/en** → 統計データ「人口推計」/「国勢調査」, table *都道府県, 年齢(5歳階級), 男女別人口* (e.g. statsData 国勢調査 2020 表 00200521) | Download CSV | `jp-admin1-pop.csv` |
| 2 | Prevalence **rates** half | MHLW 認知症施策 / 「日本における認知症の高齢者人口の将来推計」(Ninomiya, 九州大学 久山町研究) — mhlw.go.jp, search `認知症 有病率 年齢階級` | Copy the age-band prevalence % table | `jp-dementia-rates.csv` |
| 3 | Fallback rates (any country) | IHME **GBD Results** `vizhub.healthdata.org/gbd-results` → Cause *Alzheimer's & other dementias*, Measure *Prevalence*, Location *Japan* (+ subnational if listed), by age | CSV export | `jp-gbd-prev.csv` |

## 🇰🇷 South Korea

**Primary since 2026-10-27:** row 0 alone builds the KR layer (`build_kr_nid_prev60`); rows 1–3 are the old GBD×KOSIS fallback, used only when no `kr_nid/*.csv` is present.

| # | Feeds | Direct page | Export | Save as |
|---|---|---|---|---|
| 0 | **Province (17) prevalence, official estimate** | 공공데이터포털 **data.go.kr/data/15073342** 「보건복지부_시군구별 치매현황」 (yearly, 2016→; KOGL Type 1) | 「다운로드」 CSV (cp949) — plain HTTP download works, no login | `kr_nid/<original filename>.csv` |
| 1 | **Province (17) prevalence** — population half | KOSIS **kosis.kr/eng** → Population, table *Population by Administrative District (시도) and Age* (인구총조사) | Download CSV | `kr-admin1-pop.csv` |
| 2 | Prevalence **rates** half | 중앙치매센터 National Institute of Dementia — **nid.or.kr** 「대한민국 치매현황」 annual report (age-band 유병률) | Copy the age-band prevalence % table (or save the report PDF) | `kr-dementia-rates.csv` |
| 3 | Fallback rates | IHME **GBD Results** (as JP #3, Location *Republic of Korea*) | CSV export | `kr-gbd-prev.csv` |

---

## Schemas `build_data.py` expects (drop in `scripts/_data_in/`)

**Population (`*-pop.csv`)** — one row per admin unit, age split into columns.
The pipeline only needs 65+ vs total to model prevalence, but finer bands improve it:

```
unit_code,unit_name,pop_total,pop_00_64,pop_65_69,pop_70_74,pop_75_79,pop_80_84,pop_85p
JP-13,Tokyo,14047594,11900000,690000,600000,470000,320000,180000
```
- `unit_code` must match the map geometry: **TW** = TOWNCODE (8-digit) or county code; **JP** = `JP-NN` (ISO-3166-2, matches `jp-admin1.geojson`); **KR** = `KR-NN`.
- Single-year-age files (TW #77132) are fine too — the pipeline rolls them into bands.

**Prevalence rates (`*-rates.csv` / `*-dementia-rates.csv`)** — age-band → prevalence:

```
age_band,prevalence_pct
65_69,3.2
70_74,6.0
75_79,10.8
80_84,18.5
85p,35.0
```
- For **MCI** (`nhri-mci-rates.csv`) add a `mci_pct` column → drives a second map layer.

With a `{cc}-admin1-pop.csv` present (rates optional — it falls back to the crude
65+ rate), `build_data.py` emits `public/data/dementia/{cc}-modelled.json`
(`byTown` = unit_code → cases per 1,000; same shape the map reads). To surface it,
add two keys to that country's `MAP_CFG` entry in
`src/pages/projects/dementia-exposome.astro` — a one-line edit:

```js
jp: { …, layers: ['prevalence', 'pm25'], prev: '/data/dementia/jp-modelled.json' },
```

The disabled prevalence button then re-enables and the “prevalence pending” note
disappears for that country. (This step is manual on purpose — the page ships
PM2.5-only until real prevalence data exists.)

---

## Direct per-admin prevalence — 🇨🇳 China & 🇮🇳 India (ready to plug in)

Two priority countries already have **published admin-1 modelled prevalence** — the
study gives a final value per province/state, so no rates×population step is needed.
The pipeline path (`build_direct_prevalence`) + the map's per-country metric/legend
are **already built and tested**; only the data table is missing (publisher pages
403 automated fetchers, so download by hand).

| Country | Source table | Metric | Verified anchors | Save as |
|---|---|---|---|---|
| 🇨🇳 China | **Liu/Gao et al. 2024**, *Lancet Reg Health – W Pac*, "Geographical variation in dementia prevalence across China" — **Appendix G** (per-province, 28 units; CHARLS 2018) · PMC11225804 | prevalence **% among 50+** | range **2.62–13.53%**; high: Xinjiang / Jilin / Beijing; low: Fujian / Zhejiang / Guangdong | `cn-admin1-prevalence.csv` |
| 🇮🇳 India | **Lee et al. 2023**, *Alzheimer's & Dementia*, "Prevalence of dementia in India: National and state estimates" — **Table 2** (per-state/UT; LASI-DAD) · PMC10338640 / doi:10.1002/alz.12928 | prevalence **% among 60+** | national **7.4%**; **Delhi 4.5%**, **Jammu & Kashmir 11.0%** | `in-admin1-prevalence.csv` |

**Schema** (`{cc}-admin1-prevalence.csv`) — one row per admin unit:

```
unit_name,prevalence_pct
Delhi,4.5
Jammu and Kashmir,11.0
…
```
- `unit_name` is matched (case-insensitive) to the boundary name; use the English
  state/province name as it appears in `public/data/geo/{cn,in}-admin1.geojson`
  (e.g. India: `Delhi`, `Jammu and Kashmir`, `Rajasthan`; China: `Xinjiang`, `Beijing`).
  Or give `unit_code` directly (ISO-3166-2, `IN-DL` / `CN-XJ`). Unmatched rows are
  logged so you can add an alias.
- Drop the file in `scripts/_data_in/`, run `python3 scripts/build_data.py` →
  it writes `public/data/dementia/{cn,in}-modelled.json`.

**Enable on the map** (one line in `MAP_CFG`, `dementia-exposome.astro`) — the metric
+ legend (China “盛行率 50+ %”, India “盛行率 60+ %”) are already wired:

```js
cn: { …, layers: ['prevalence', 'pm25'], prevMetric: PREV_CN, prev: '/data/dementia/cn-modelled.json', … },
in: { …, layers: ['prevalence', 'pm25'], prevMetric: PREV_IN, prev: '/data/dementia/in-modelled.json', … },
```

> **Metric caveat:** these are **% among the elderly** (CN 50+, IN 60+), a different
> denominator from Taiwan's *cases per 1,000 all-ages* — hence per-country legends.
> Don't compare the prevalence colours across countries; compare within a country.
> Both remain **modelled estimates**, not measured. States/provinces not in the study
> (India ~18 of 36; China excludes Tibet/Ningxia/Hainan) stay grey until filled.

---

## Reachability legend (tested from the build sandbox, July 2026)
- ✅ reachable & already wired: ACAG S3, Natural Earth, npm/GitHub, pypi.
- 🔒 policy-blocked here → **this list** (you download; Playwright can't bypass it here).
- 🔑 account/query/agreement: NHIRD (TW), GBD export (free login), KOSIS bulk API key.
