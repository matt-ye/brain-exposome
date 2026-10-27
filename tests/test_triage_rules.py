"""Offline checks for ledger-based triage (no network, no LLM).

The facts below are what the LLM should read out of real abstracts the monthly radar
surfaced in 2026-08..10; the expected buckets are the owner's review of those papers.
Run: python -m unittest tests/test_triage_rules.py
"""
import os
import sys
import unittest
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import triage_rules as tr  # noqa: E402

TODAY = datetime.date(2026, 10, 27)
LEDGER = {
    "defaults": {
        "prevalence": {"natSrc": "GBD 2023 (IHME) 60+", "natYear": "2023"},
        "mci": {"natSrc": "—", "natYear": "—"},
        "obesity": {"natSrc": "NCD-RisC BMI≥30 (adult, age-std)", "natYear": "2024"},
    },
    "overrides": {
        "CN": {"prevalence": {"natSrc": "Jia 2020", "natYear": "2015–18",
                              "subSrc": "Liu-Gao 2024（28 省）", "subYear": "2018"}},
        "KR": {"prevalence": {"natSrc": "NaSDEK / 중앙치매센터", "natYear": "2023"}},
        "US": {"prevalence": {"natSrc": "Alzheimer's Assoc. F&F", "natYear": "2025",
                              "subSrc": "各州（Alz Assoc）", "subYear": "2025"}},
        "TW": {"prevalence": {"natSrc": "NHRI 全國調查", "natYear": "2019–2023"}},
        "JP": {"prevalence": {"natSrc": "Hisayama/Ninomiya", "natYear": "2022",
                              "subSrc": "Toyama 都道府県推估", "subYear": "2020–45"}},
    },
}


def facts(**kw):
    base = {"estimate": True, "layer": "prevalence", "measure": "prevalence", "population": "general",
            "method": "survey", "countries": [], "data_end_year": None, "dataset": ""}
    base.update(kw)
    return base


class EndYear(unittest.TestCase):
    def test_ranges_and_projections(self):
        self.assertEqual(tr.end_year("2015–18", TODAY), 2018)
        self.assertEqual(tr.end_year("2019–2023", TODAY), 2023)
        self.assertEqual(tr.end_year("2020–45", TODAY), 2020)   # projection: data stop at the start
        self.assertIsNone(tr.end_year("—", TODAY))


class RealCases(unittest.TestCase):
    def decide(self, f):
        return tr.decide(f, LEDGER, TODAY)[0]

    def test_korea_newer_than_ledger_is_kept(self):        # Jung 2026, data to 2025 vs NID 2023
        self.assertEqual(self.decide(facts(countries=["KR"], data_end_year=2025, method="administrative",
                                           dataset="NHIS")), tr.UPDATE)

    def test_china_new_paper_old_data_is_dropped(self):    # CMHS 2013–15 vs CHARLS 2018 in use
        b, why = tr.decide(facts(countries=["CN"], data_end_year=2015, dataset="CMHS"), LEDGER, TODAY)
        self.assertEqual(b, tr.OLDER)
        self.assertIn("2018", why)

    def test_same_dataset_as_global_default_is_dropped(self):   # Europe, GBD 2023
        self.assertEqual(self.decide(facts(countries=["GLOBAL"], data_end_year=2023,
                                           dataset="GBD 2023")), tr.OLDER)

    def test_us_survey_older_than_alz_assoc_is_dropped(self):   # NHATS 2023 vs F&F 2025
        self.assertEqual(self.decide(facts(countries=["US"], data_end_year=2023, dataset="NHATS")), tr.OLDER)

    def test_subgroup_is_not_an_estimate(self):            # SCD in cancer survivors; BPSD in patients
        self.assertEqual(self.decide(facts(layer="scd", countries=["US"], data_end_year=2024,
                                           population="subgroup")), tr.NOT_ESTIMATE)

    def test_claims_diagnosed_mci_is_not_prevalence(self):  # Taiwan MCI from insurance claims
        self.assertEqual(self.decide(facts(layer="mci", countries=["TW"], data_end_year=2023,
                                           method="administrative")), tr.NOT_ESTIMATE)

    def test_layer_without_source_is_a_gap(self):           # MCI default is "—"
        self.assertEqual(self.decide(facts(layer="mci", countries=["NG"], data_end_year=2022)), tr.UPDATE)

    def test_relative_risk_cannot_update_a_prevalence_layer(self):
        self.assertEqual(self.decide(facts(measure="relative_risk", countries=["GLOBAL"])), tr.NOT_ESTIMATE)

    def test_same_dataset_without_a_year_is_still_same_data(self):   # hearing loss × GBD, no year
        self.assertEqual(self.decide(facts(countries=["GLOBAL"], dataset="GBD")), tr.OLDER)

    def test_unknown_year_fails_open(self):
        self.assertEqual(self.decide(facts(countries=["CN"])), tr.UPDATE)

    def test_projection_ledger_entry_compares_on_its_base_year(self):   # JP Toyama 2020–45
        self.assertEqual(self.decide(facts(countries=["JP"], data_end_year=2022, dataset="JPSC-AD")), tr.UPDATE)

    def test_multi_country_keeps_if_any_country_is_newer(self):
        self.assertEqual(self.decide(facts(countries=["CN", "KR"], data_end_year=2024)), tr.UPDATE)

    def test_single_cohort_cannot_update_pooled_risk_layer(self):   # NSCLC → dementia, Taiwan NHIRD
        self.assertEqual(self.decide(facts(layer="paf", measure="relative_risk", method="administrative",
                                           countries=["TW"])), tr.NOT_ESTIMATE)

    def test_meta_analysis_can_update_pooled_risk_layer(self):
        ledger = {**LEDGER, "defaults": {**LEDGER["defaults"],
                                         "paf": {"natSrc": "5 因子 × Livingston 2024 RRs", "natYear": "2015–2025"}}}
        self.assertEqual(tr.decide(facts(layer="paf", measure="relative_risk", method="meta_analysis",
                                         countries=["GLOBAL"], data_end_year=2026), ledger, TODAY)[0], tr.UPDATE)

    def test_local_cohort_cannot_update_a_national_figure(self):   # pooled TW community cohorts
        self.assertEqual(self.decide(facts(countries=["TW"], data_end_year=2025, representative=False)),
                         tr.NOT_ESTIMATE)

    def test_not_an_estimate(self):
        self.assertEqual(self.decide(facts(estimate=False, layer="none")), tr.NOT_ESTIMATE)


if __name__ == "__main__":
    unittest.main()
