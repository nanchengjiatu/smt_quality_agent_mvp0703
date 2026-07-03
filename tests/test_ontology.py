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
        self.assertEqual(snapshot["version"], "spi-printing-v4")
        concept_ids = {item["id"] for item in snapshot["concepts"]}
        self.assertIn("process.solder_paste_printing", concept_ids)
        self.assertIn("inspection.spi", concept_ids)
        self.assertIn("scope.consecutive_same_pad", concept_ids)
        self.assertTrue(snapshot["relations"])

    def test_mechanism_defect_relations_are_generated_from_direction(self) -> None:
        # 机理→缺陷方向的边由 direction 属性生成,图与机理目录不会漂移。
        snapshot = ontology_snapshot()
        edges = {
            (item["subject"], item["object"])
            for item in snapshot["relations"]
            if item["predicate"] == "causes_defect"
        }
        self.assertIn(("mech.aperture_clogging", "defect.insufficient_volume"), edges)
        self.assertIn(("mech.understencil_residue", "defect.over_volume"), edges)
        # 双向机理两条边都有。
        self.assertIn(("mech.poor_gasketing", "defect.over_volume"), edges)
        self.assertIn(("mech.poor_gasketing", "defect.insufficient_volume"), edges)

    def test_deprecated_root_causes_point_to_their_mechanism(self) -> None:
        snapshot = ontology_snapshot()
        mechanisms = {
            item["id"] for item in snapshot["concepts"]
            if item["type"] == "FailureMechanism"
        }
        for item in snapshot["concepts"]:
            if item["type"] != "RootCauseCandidate":
                continue
            props = item.get("properties") or {}
            if props.get("deprecated"):
                self.assertIn(props.get("mechanism"), mechanisms, item["id"])

    def test_v3_layers_are_present(self) -> None:
        snapshot = ontology_snapshot()
        by_type: dict[str, int] = {}
        for item in snapshot["concepts"]:
            by_type[item["type"]] = by_type.get(item["type"], 0) + 1
        # 实体层骨架 + 三个正交轴 + 机理层
        self.assertEqual(by_type["SpatialExtent"], 4)
        self.assertEqual(by_type["TemporalPattern"], 4)
        self.assertEqual(by_type["DataValidity"], 3)
        self.assertEqual(by_type["ProcessStage"], 4)
        self.assertGreaterEqual(by_type["EquipmentElement"], 7)
        self.assertEqual(by_type["FailureMechanism"], 13)

    def test_v2_scopes_are_deprecated_but_still_mapped(self) -> None:
        snapshot = ontology_snapshot()
        for item in snapshot["concepts"]:
            if item["type"] == "AbnormalScope":
                self.assertTrue(
                    (item.get("properties") or {}).get("deprecated"),
                    item["id"],
                )

    def test_mechanism_evidence_references_are_registered(self) -> None:
        snapshot = ontology_snapshot()
        evidence_ids = {
            item["id"] for item in snapshot["concepts"]
            if item["type"] == "EvidenceType"
        }
        element_ids = {
            item["id"] for item in snapshot["concepts"]
            if item["type"] in {"EquipmentElement", "Material"}
        }
        stage_ids = {
            item["id"] for item in snapshot["concepts"]
            if item["type"] == "ProcessStage"
        }
        for item in snapshot["concepts"]:
            if item["type"] != "FailureMechanism":
                continue
            props = item["properties"]
            self.assertIn(props["element"], element_ids, item["id"])
            self.assertIn(props["stage"], stage_ids, item["id"])
            for evidence_id in [*props["auto_checks"], *props["manual_checks"]]:
                self.assertIn(evidence_id, evidence_ids, f"{item['id']} -> {evidence_id}")

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
        # 机理 label 是权威根因词表,映射到机理概念。
        ids = ontology_ids_for(
            direction="多锡",
            scope="单Pad孤立异常",
            cause="钢网底部残锡转印",
        )
        self.assertEqual(ids["direction"], "defect.over_volume")
        self.assertEqual(ids["scope"], "scope.single_pad_isolated")
        self.assertEqual(ids["cause"], "mech.understencil_residue")

    def test_legacy_cause_labels_still_resolve(self) -> None:
        # 旧记录里的 v3 措辞仍能解析到废弃的 RootCauseCandidate 概念。
        ids = ontology_ids_for(cause="钢网单孔底部残锡或开口异常")
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
