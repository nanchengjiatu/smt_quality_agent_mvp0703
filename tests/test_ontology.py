import unittest

from smt_quality_agent.ontology import (
    CONCEPTS,
    SCOPE_TO_CONCEPT_ID,
    concept_by_id,
    ontology_ids_for,
    ontology_snapshot,
)


class OntologyTest(unittest.TestCase):
    def test_snapshot_exposes_core_concepts_and_relations(self) -> None:
        snapshot = ontology_snapshot()
        self.assertEqual(snapshot["version"], "spi-printing-v2")
        concept_ids = {item["id"] for item in snapshot["concepts"]}
        self.assertIn("process.solder_paste_printing", concept_ids)
        self.assertIn("inspection.spi", concept_ids)
        self.assertIn("scope.consecutive_same_pad", concept_ids)
        self.assertTrue(snapshot["relations"])

    def test_every_scope_label_used_by_analyses_is_registered(self) -> None:
        # Drilldown categories.
        for label in ("单Pad孤立异常", "同元件多Pad异常", "局部区域", "整板同向", "疑似SPI假异常"):
            self.assertIn(label, SCOPE_TO_CONCEPT_ID, label)
        # Realtime patterns.
        for label in ("同点多板异常", "整板趋势异常", "单点偶发异常"):
            self.assertIn(label, SCOPE_TO_CONCEPT_ID, label)
        # Legacy alias resolves to the canonical concept.
        self.assertEqual(
            SCOPE_TO_CONCEPT_ID["同一元件多Pad异常"],
            SCOPE_TO_CONCEPT_ID["同元件多Pad异常"],
        )

    def test_realtime_and_drilldown_same_pad_scopes_are_distinct(self) -> None:
        # 严格连续（下钻触发）与跨板重复（实时）是不同判定口径，必须是
        # 两个概念，不能共用一个标签。
        self.assertNotEqual(
            SCOPE_TO_CONCEPT_ID["连续3板同点异常"],
            SCOPE_TO_CONCEPT_ID["同点多板异常"],
        )

    def test_maps_labels_to_stable_ids(self) -> None:
        ids = ontology_ids_for(
            direction="多锡",
            scope="单Pad孤立异常",
            cause="钢网单孔底部残锡或开口异常",
        )
        self.assertEqual(ids["direction"], "defect.over_volume")
        self.assertEqual(ids["scope"], "scope.single_pad_isolated")
        self.assertEqual(ids["cause"], "root_cause.stencil_single_aperture_residue")

    def test_spi_false_alarm_category_maps_to_scope_concept(self) -> None:
        ids = ontology_ids_for(direction="多锡", scope="疑似SPI假异常")
        self.assertEqual(ids["scope"], "scope.suspected_spi_false_alarm")

    def test_mappings_are_generated_from_concepts(self) -> None:
        snapshot = ontology_snapshot()
        scope_map = snapshot["mappings"]["scope"]
        scope_concepts = [item for item in snapshot["concepts"] if item["type"] == "AbnormalScope"]
        for concept in scope_concepts:
            self.assertEqual(scope_map[concept["label"]], concept["id"])
            for alias in concept["aliases"]:
                self.assertIn(alias, scope_map)

    def test_concept_ids_are_unique(self) -> None:
        ids = [concept.id for concept in CONCEPTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_concept_lookup_returns_serializable_payload(self) -> None:
        concept = concept_by_id("defect.over_volume")
        self.assertIsNotNone(concept)
        self.assertEqual(concept["label"], "多锡")


if __name__ == "__main__":
    unittest.main()
