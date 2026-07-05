import unittest

from smt_quality_agent.ontology import (
    CONCEPTS,
    concept_by_id,
    ontology_ids_for,
    ontology_snapshot,
)


class OntologyTest(unittest.TestCase):
    def test_snapshot_exposes_core_concepts_and_relations(self) -> None:
        snapshot = ontology_snapshot()
        self.assertEqual(snapshot["version"], "spi-printing-v5")
        concept_ids = {item["id"] for item in snapshot["concepts"]}
        self.assertIn("process.solder_paste_printing", concept_ids)
        self.assertIn("inspection.spi", concept_ids)
        self.assertIn("mech.aperture_clogging", concept_ids)
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

    def test_no_deprecated_concepts_remain(self) -> None:
        # v5 已删除全部废弃词表(v2 AbnormalScope、v3 根因措辞、v3 ActionType);
        # 新概念一律不得以 deprecated 形式入库,直接删。
        snapshot = ontology_snapshot()
        types = {item["type"] for item in snapshot["concepts"]}
        self.assertNotIn("AbnormalScope", types)
        self.assertNotIn("ActionType", types)
        for item in snapshot["concepts"]:
            self.assertFalse(
                (item.get("properties") or {}).get("deprecated"),
                item["id"],
            )

    def test_root_cause_vocabulary_is_only_trend_and_fallback(self) -> None:
        # 根因显示文本的权威是机理 label;RootCauseCandidate 只保留机理锁定
        # 不了的三条:两条趋势归因 + 一条证据不足兜底。
        ids = {
            item["id"] for item in ontology_snapshot()["concepts"]
            if item["type"] == "RootCauseCandidate"
        }
        self.assertEqual(ids, {
            "root_cause.cumulative_state_degradation",
            "root_cause.discrete_process_change",
            "root_cause.local_printing_state",
        })

    def test_layers_are_present(self) -> None:
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

    def test_maps_labels_to_stable_ids(self) -> None:
        # 机理 label 是权威根因词表,映射到机理概念;范围没有单独 ID,
        # 权威表达是契约 scope 里的三轴概念 ID。
        ids = ontology_ids_for(direction="多锡", cause="钢网底部残锡转印")
        self.assertEqual(ids["direction"], "defect.over_volume")
        self.assertEqual(ids["cause"], "mech.understencil_residue")
        self.assertNotIn("scope", ids)

    def test_trend_attribution_labels_resolve(self) -> None:
        ids = ontology_ids_for(cause="随生产累积的钢网或锡膏状态劣化")
        self.assertEqual(ids["cause"], "root_cause.cumulative_state_degradation")

    def test_legacy_v3_cause_labels_no_longer_resolve(self) -> None:
        # v3 措辞词表已随 v5 删除,旧措辞不再映射(输出侧从 v4 起只发机理 label)。
        ids = ontology_ids_for(cause="钢网单孔底部残锡或开口异常")
        self.assertNotIn("cause", ids)

    def test_mappings_expose_direction_and_cause_only(self) -> None:
        snapshot = ontology_snapshot()
        self.assertEqual(set(snapshot["mappings"].keys()), {"direction", "cause"})
        for item in snapshot["concepts"]:
            if item["type"] == "FailureMechanism":
                self.assertEqual(snapshot["mappings"]["cause"][item["label"]], item["id"])

    def test_concept_ids_are_unique(self) -> None:
        ids = [concept.id for concept in CONCEPTS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_concept_lookup_returns_serializable_payload(self) -> None:
        concept = concept_by_id("defect.over_volume")
        self.assertIsNotNone(concept)
        self.assertEqual(concept["label"], "多锡")


if __name__ == "__main__":
    unittest.main()
