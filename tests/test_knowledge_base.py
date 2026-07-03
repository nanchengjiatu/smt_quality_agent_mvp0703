import unittest

from smt_quality_agent.knowledge_base import (
    RULES,
    abnormal_cause_candidates,
    disposition_for,
    enrich_with_ontology_ids,
    event_cause_candidates,
    event_scope_for_category,
    rule_by_id,
    rule_catalog,
    scope_root_cause_candidate,
)
from smt_quality_agent.ontology import SCOPE_TO_CONCEPT_ID


class RuleRegistryTest(unittest.TestCase):
    def test_every_rule_has_uniform_schema(self) -> None:
        for rule in RULES:
            self.assertTrue(rule["id"].startswith("rule."), rule["id"])
            self.assertIn("rule_type", rule)
            self.assertIn("condition", rule)
            self.assertIn("source", rule)
            self.assertIn("cause", rule)
            self.assertTrue(rule.get("action") or rule.get("action_template"), rule["id"])
            self.assertIn("confidence_base", rule, rule["id"])

    def test_rule_ids_are_unique(self) -> None:
        ids = [rule["id"] for rule in RULES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_scope_rule_conditions_use_registered_vocabulary(self) -> None:
        # 规则条件里的 scope/pattern 必须是本体注册过的词，杜绝并行词表。
        for rule in RULES:
            scope = rule["condition"].get("scope") or rule["condition"].get("abnormal_pattern")
            if scope and scope != "*":
                self.assertIn(scope, SCOPE_TO_CONCEPT_ID, f"{rule['id']}: {scope}")

    def test_confidence_ladder_orders_direct_evidence_above_candidates(self) -> None:
        spi = rule_by_id("rule.spi_false_alarm_review")
        scope = rule_by_id("rule.over_volume_single_pad")
        event = rule_by_id("rule.event_over_volume_local_stencil_residue")
        fallback = rule_by_id("rule.fallback_local_printing_state")
        self.assertGreater(spi["confidence_base"], scope["confidence_base"])
        self.assertGreater(scope["confidence_base"], event["confidence_base"])
        self.assertGreater(event["confidence_base"], fallback["confidence_base"])


class LookupTest(unittest.TestCase):
    def test_scope_root_cause_candidate_carries_rule_identity(self) -> None:
        candidate = scope_root_cause_candidate("多锡", "单Pad孤立异常", "detail")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["rule_id"], "rule.over_volume_single_pad")
        self.assertEqual(candidate["rule_source"], "knowledge_base")
        self.assertEqual(candidate["cause"], "钢网单孔底部残锡或开口异常")
        self.assertIn("单 Pad", candidate["evidence"])
        self.assertEqual(candidate["evidence_level"], "中")

    def test_event_scope_grouping_axis(self) -> None:
        self.assertEqual(event_scope_for_category("整板同向"), "整板大面积")
        self.assertEqual(event_scope_for_category("整板趋势异常"), "整板大面积")
        self.assertEqual(event_scope_for_category("单Pad孤立异常"), "局部焊盘")
        self.assertEqual(event_scope_for_category("同元件多Pad异常"), "局部焊盘")

    def test_event_cause_candidates_carry_rule_identity(self) -> None:
        candidates = event_cause_candidates("少锡", "整板大面积")
        self.assertTrue(candidates)
        self.assertTrue(all(item["rule_id"].startswith("rule.") for item in candidates))
        self.assertTrue(all(item["rule_source"] == "knowledge_base.event_rules" for item in candidates))
        self.assertIn("锡膏供给", candidates[0]["cause"])

    def test_abnormal_cause_candidates_carry_rule_identity(self) -> None:
        candidates = abnormal_cause_candidates("多锡", "同点多板异常", "高")
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["rule_id"], "rule.abnormal_over_repeat_stencil_residue")
        self.assertEqual(candidates[0]["rule_source"], "knowledge_base.abnormal_rules")
        self.assertEqual(candidates[0]["cause"], "钢网底部残锡")

    def test_abnormal_cause_candidates_fall_back_to_observation(self) -> None:
        candidates = abnormal_cause_candidates("多锡", "单点偶发异常", "低")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule_id"], "rule.abnormal_observe_next_board")


class RuleCatalogTest(unittest.TestCase):
    def test_rule_catalog_exposes_reviewable_rules(self) -> None:
        catalog = rule_catalog()
        self.assertEqual(catalog["version"], "rule-catalog-v4")
        self.assertGreater(catalog["rule_count"], 20)
        self.assertEqual(catalog["rule_count"], len(catalog["rules"]))
        rule_ids = [rule["rule_id"] for rule in catalog["rules"]]
        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        self.assertIn("rule.over_volume_single_pad", rule_ids)
        self.assertIn("rule.insufficient_volume_board_same_direction", rule_ids)
        self.assertIn("rule.event_over_volume_board_paste_viscosity", rule_ids)
        self.assertIn("rule.abnormal_over_repeat_stencil_residue", rule_ids)
        self.assertIn("rule.review_gasketing_board_support", rule_ids)
        self.assertIn("disposition.widened_scope", rule_ids)
        self.assertTrue(all("condition" in rule and "output" in rule for rule in catalog["rules"]))

    def test_scope_rules_are_written_as_review_cards(self) -> None:
        catalog = rule_catalog()
        over_single = next(rule for rule in catalog["rules"] if rule["rule_id"] == "rule.over_volume_single_pad")
        self.assertIn("单 Pad", over_single["output"]["applies_when"])
        self.assertIn("不应只按单孔处理", over_single["output"]["not_sufficient_when"])
        self.assertIn("原始 SPI 图像", over_single["output"]["first_check"])
        self.assertIn("连续复判 3 块", over_single["output"]["recheck_method"])
        self.assertIn("原始 SPI 图像", over_single["output"]["evidence_required"])

    def test_catalog_has_no_generated_boilerplate(self) -> None:
        # 复核卡片字段只在专家手写过的规则上出现，事件/实时候选规则不再
        # 用模板套话填充 applies_when。
        catalog = rule_catalog()
        event_rule = next(
            rule for rule in catalog["rules"]
            if rule["rule_id"] == "rule.event_over_volume_local_paste_state"
        )
        self.assertNotIn("applies_when", event_rule["output"])
        self.assertNotIn("first_check", event_rule["output"])

    def test_squeegee_pressure_rules_do_not_guess_adjustment_direction(self) -> None:
        catalog = rule_catalog()
        pressure_rules = [
            rule for rule in catalog["rules"]
            if "squeegee_pressure" in rule["rule_id"]
        ]
        self.assertTrue(pressure_rules)
        for rule in pressure_rules:
            output_text = f"{rule['output'].get('cause', '')} {rule['output'].get('action', '')}"
            self.assertIn("现场确认", output_text)
            self.assertNotIn("适当提高", output_text)
            self.assertNotIn("适当降低", output_text)


class DispositionTest(unittest.TestCase):
    def test_disposition_prioritizes_widened_scope(self) -> None:
        disposition = disposition_for(
            data_status="pass",
            spi_status="pass",
            category="局部区域",
            recovery_kind="recovered",
            confidence="中",
        )
        self.assertEqual(disposition["priority"], "P1")
        self.assertIn("现场排查", disposition["disposition"])


class OntologyEnrichmentTest(unittest.TestCase):
    def test_enriches_rule_output_with_ontology_ids(self) -> None:
        enriched = enrich_with_ontology_ids({
            "direction": "少锡",
            "category": "同元件多Pad异常",
            "cause": "钢网单孔堵塞或脱模不良",
        })
        self.assertEqual(enriched["ontology_ids"]["direction"], "defect.insufficient_volume")
        self.assertEqual(enriched["ontology_ids"]["scope"], "scope.component_multi_pad")
        self.assertEqual(enriched["ontology_ids"]["cause"], "root_cause.stencil_single_aperture_blockage")


if __name__ == "__main__":
    unittest.main()
