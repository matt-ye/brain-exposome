"""Offline checks for the literature radar's de-duplication (no network).

Run: python -m unittest tests/test_literature_watch.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import literature_watch as lw  # noqa: E402


DOI_REC = {"doi": "10.1000/ABC.123", "title": "A trial.", "firstPublicationDate": "2026-10-01",
           "journalTitle": "J Rehab"}
EPMC_REC = {"source": "PPR", "id": "PPR999", "title": "A preprint", "firstPublicationDate": "2026-10-02"}
# The same article as Europe PMC returns it twice: MEDLINE record with a DOI, PMC
# record without — and with curly instead of straight quotes (real case, 2026-10 OT run).
MED_REC = {"doi": "10.1111/hex.70918", "title": "'Some of Them Were Created by People': Stroke Study"}
PMC_REC = {"source": "PMC", "id": "PMC13648021",
           "title": "‘Some of Them Were Created by People’: Stroke Study"}


class PaperKeys(unittest.TestCase):
    def test_doi_key_is_case_insensitive(self):
        self.assertEqual(lw.paper_keys(DOI_REC), lw.paper_keys({**DOI_REC, "doi": "10.1000/abc.123"}))

    def test_med_and_pmc_records_of_one_paper_share_a_key(self):
        self.assertTrue(lw.paper_keys(MED_REC) & lw.paper_keys(PMC_REC))

    def test_different_papers_share_no_key(self):
        self.assertFalse(lw.paper_keys(DOI_REC) & lw.paper_keys(EPMC_REC))


class ReadBack(unittest.TestCase):
    """What a report prints must read back to the same keys — the whole cross-month
    de-dup rests on this round trip."""

    def test_report_lines_round_trip_to_keys(self):
        body = "\n".join([lw.line_for(DOI_REC), lw.line_for(EPMC_REC, {"reason": "OT RCT"})])
        self.assertEqual(lw.keys_in_text(body), lw.paper_keys(DOI_REC) | lw.paper_keys(EPMC_REC))

    def test_text_without_links_yields_nothing(self):
        self.assertEqual(lw.keys_in_text("- none in the last 45 days"), set())


class SeenFromIssues(unittest.TestCase):
    def test_missing_env_fails_open_with_reason(self):
        saved = {k: os.environ.pop(k, None) for k in ("GITHUB_REPOSITORY", "GH_TOKEN", "GITHUB_TOKEN")}
        try:
            keys, err = lw.seen_from_issues("data-refresh")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(keys, set())
        self.assertTrue(err)


if __name__ == "__main__":
    unittest.main()
