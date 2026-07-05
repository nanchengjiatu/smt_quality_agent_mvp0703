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
        # 机理→缺陷方向的生成边与机理属性边。
        self.assertIn("smt:causesDefect smt:defect.insufficient_volume", ttl)
        self.assertIn("smt:autoCheck smt:evidence.trend_slope", ttl)
        self.assertIn("smt:canonicalAction", ttl)
        # 兜底归因措辞标注对应机理。
        self.assertIn("smt:expressesMechanism smt:mech.undetermined", ttl)


if __name__ == "__main__":
    unittest.main()
