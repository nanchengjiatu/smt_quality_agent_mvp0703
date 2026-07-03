import unittest

from smt_quality_agent.knowledge_base import (
    DECISION_RULES,
    RULES,
    RULES_WITHOUT_MECHANISM,
    abnormal_cause_candidates,
    cleaning_cycle_aligned,
    diagnose,
    disposition_for,
    enrich_with_ontology_ids,
    event_cause_candidates,
    event_scope_for_category,
    match_metric_signature,
    rule_by_id,
    rule_catalog,
    scope_root_cause_candidate,
)
from smt_quality_agent.ontology import MECHANISMS, SCOPE_TO_CONCEPT_ID


def sample_observation(**overrides) -> dict:
    observation = {
        "direction": "多锡",
        "category": "单Pad孤立异常",
        "spatial": "spatial.single_pad",
        "temporal": "temporal.consecutive",
        "validity": "validity.valid",
        "scope_detail": "同元件和全量窗口没有显示明显扩散。",
        "trend_kind": "step",
        "trend_detail": "触发段均值跳升。",
        "drifted_parameters": [],
        "cross_model_baseline": False,
        "recovery_kind": "recovered",
        "recovery_parameters": [],
        "recovery_detail": "",
        "periodic": False,
        "periodic_gap": None,
        "periodicity_detail": "",
        "cleaning_frequency": None,
        "metric_signature": {},
        "metric_signature_text": "",
        "spi_detail": "",
        "data_status": "pass",
    }
    observation.update(overrides)
    return observation


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


class MechanismBindingTest(unittest.TestCase):
    def test_every_rule_binds_a_registered_mechanism_or_is_exempt(self) -> None:
        for rule in RULES:
            if rule["id"] in RULES_WITHOUT_MECHANISM:
                self.assertIsNone(rule["mechanism"], rule["id"])
                continue
            self.assertIn(rule["mechanism"], MECHANISMS, rule["id"])

    def test_mechanisms_declare_required_domain_attributes(self) -> None:
        for mechanism_id, mechanism in MECHANISMS.items():
            props = mechanism.get("properties") or {}
            for key in ("element", "stage", "direction", "onset",
                        "auto_checks", "manual_checks"):
                self.assertIn(key, props, f"{mechanism_id} missing {key}")


class DecisionLayerTest(unittest.TestCase):
    def test_decision_rules_are_ordered_gates_first(self) -> None:
        orders = [rule["order"] for rule in DECISION_RULES]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(DECISION_RULES[0]["id"], "decide.spi_false_alarm_gate")

    def test_spi_gate_outranks_everything(self) -> None:
        result = diagnose(sample_observation(
            validity="validity.spi_suspect",
            category="疑似SPI假异常",
            spi_detail="主指标偏差不明显",
        ))
        primary = result["root_cause_assessment"][0]
        self.assertEqual(primary["rule_id"], "rule.spi_false_alarm_review")
        self.assertEqual(primary["mechanism_id"], "mech.spi_false_call")
        self.assertEqual(result["confidence"], "高")

    def test_cross_model_baseline_downgrades_drift_confidence(self) -> None:
        result = diagnose(sample_observation(
            drifted_parameters=["printspeed"], cross_model_baseline=True,
        ))
        drift = next(
            item for item in result["root_cause_assessment"]
            if item["rule_id"] == "rule.parameter_drift"
        )
        self.assertEqual(drift["confidence"], 0.6)
        self.assertEqual(drift["evidence_level"], "中")

    def test_data_suspect_lowers_overall_confidence(self) -> None:
        result = diagnose(sample_observation(data_status="review"))
        self.assertEqual(result["confidence"], "低")

    def test_diagnose_caps_and_ranks_candidates(self) -> None:
        result = diagnose(sample_observation())
        assessments = result["root_cause_assessment"]
        self.assertLessEqual(len(assessments), 3)
        confidences = [item["confidence"] for item in assessments]
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        self.assertEqual([item["priority"] for item in assessments],
                         list(range(1, len(assessments) + 1)))


class SignatureMatchTest(unittest.TestCase):
    def test_full_match_needs_two_evaluated_metrics(self) -> None:
        status, _ = match_metric_signature(
            "avdp:down,aadp:down|flat,ahdp:down",
            {"avdp": "down", "aadp": "flat", "ahdp": "down"},
        )
        self.assertEqual(status, "matched")

    def test_opposite_direction_is_hard_conflict(self) -> None:
        status, detail = match_metric_signature(
            "avdp:up,aadp:up,ahdp:flat|down",
            {"avdp": "up", "aadp": "down", "ahdp": "flat"},
        )
        self.assertEqual(status, "conflict")
        self.assertIn("aadp", detail)

    def test_flat_versus_up_is_partial(self) -> None:
        status, _ = match_metric_signature(
            "avdp:up,aadp:up,ahdp:flat|down",
            {"avdp": "up", "aadp": "flat", "ahdp": "flat"},
        )
        self.assertEqual(status, "partial")

    def test_no_constraints_or_no_observation_is_unknown(self) -> None:
        self.assertEqual(match_metric_signature("", {"avdp": "up"})[0], "unknown")
        self.assertEqual(
            match_metric_signature("avdp:any,aadp:any,ahdp:any", {"avdp": "up"})[0],
            "unknown",
        )
        self.assertEqual(match_metric_signature("avdp:up", {})[0], "unknown")


class CleaningAlignmentTest(unittest.TestCase):
    def test_integer_multiple_within_tolerance_aligns(self) -> None:
        self.assertTrue(cleaning_cycle_aligned(9.0, 3.0))
        self.assertTrue(cleaning_cycle_aligned(9.5, 3.0))

    def test_tolerance_is_per_cycle_not_per_multiple(self) -> None:
        # 13 相对 12(4×3)差 1 板 > 0.2×3,不允许因倍数大而放宽。
        self.assertFalse(cleaning_cycle_aligned(13.0, 3.0))
        self.assertFalse(cleaning_cycle_aligned(7.5, 3.0))

    def test_missing_data_never_aligns(self) -> None:
        self.assertFalse(cleaning_cycle_aligned(None, 3.0))
        self.assertFalse(cleaning_cycle_aligned(9.0, None))

    def test_alignment_boosts_periodic_candidate(self) -> None:
        result = diagnose(sample_observation(
            periodic=True, periodic_gap=9.0, cleaning_frequency=3.0,
        ))
        periodic = next(
            item for item in result["root_cause_assessment"]
            if item["rule_id"] == "rule.periodic_maintenance_cycle"
        )
        self.assertEqual(periodic["confidence"], 0.92)
        reference = next(
            check for check in periodic["auto_checks"]
            if check["evidence_id"] == "evidence.cleaning_frequency_reference"
        )
        self.assertEqual(reference["status"], "核验通过")


class ParameterGroupingTest(unittest.TestCase):
    def test_release_parameters_nominate_poor_release_mechanism(self) -> None:
        result = diagnose(sample_observation(
            drifted_parameters=["snapoffspeed", "printspeed"],
        ))
        by_rule = {item["rule_id"]: item for item in result["root_cause_assessment"]}
        self.assertIn("rule.release_parameter_drift", by_rule)
        release = by_rule["rule.release_parameter_drift"]
        self.assertEqual(release["mechanism_id"], "mech.poor_release")
        self.assertIn("snapoffspeed", release["evidence"])
        self.assertIn("rule.parameter_drift", by_rule)
        self.assertIn("printspeed", by_rule["rule.parameter_drift"]["evidence"])

    def test_grouped_parameters_do_not_duplicate_generic_rule(self) -> None:
        result = diagnose(sample_observation(drifted_parameters=["cleaningspeed"]))
        rule_ids = [item["rule_id"] for item in result["root_cause_assessment"]]
        self.assertIn("rule.cleaning_parameter_drift", rule_ids)
        self.assertNotIn("rule.parameter_drift", rule_ids)


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
        self.assertEqual(catalog["version"], "rule-catalog-v5")
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
        # 规则页按"决策梯 → 机理 → 处置"组织,目录必须带机理摘要。
        self.assertEqual(len(catalog["mechanisms"]), 13)
        clogging = next(
            item for item in catalog["mechanisms"]
            if item["mechanism_id"] == "mech.aperture_clogging"
        )
        self.assertEqual(clogging["element"], "钢网开口")
        self.assertTrue(any(
            check["availability"] == "not_collected" for check in clogging["auto_checks"]
        ))

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
