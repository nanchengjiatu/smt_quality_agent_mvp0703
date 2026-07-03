import unittest
from pathlib import Path

from smt_quality_agent.ontology import to_turtle


class OntologyTurtleTest(unittest.TestCase):
    def test_ttl_file_matches_generated_output(self) -> None:
        """docs/smt_quality_ontology.ttl is generated from ontology.py; any
        drift means someone edited the TTL by hand or forgot to regenerate
        (python3 -m smt_quality_agent.ontology)."""
        ttl = Path("docs/smt_quality_ontology.ttl").read_text(encoding="utf-8")
        self.assertEqual(ttl, to_turtle())

    def test_generated_ttl_contains_core_shapes(self) -> None:
        ttl = to_turtle()
        self.assertIn("smt:verifiedBy smt:inspection.spi", ttl)
        self.assertIn("smt:observes smt:defect.over_volume", ttl)
        self.assertIn("smt:requiresEvidence smt:evidence.full_spi_window", ttl)
        self.assertIn("smt:scope.suspected_spi_false_alarm", ttl)
        self.assertIn("smt:hasCandidateCause smt:root_cause.spi_program_false_alarm", ttl)


if __name__ == "__main__":
    unittest.main()
