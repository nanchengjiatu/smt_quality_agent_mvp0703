import unittest

from smt_quality_agent.knowledge_base import (
    CAUSE_OVERRIDES,
    DECISION_RULES,
    DISPOSITION_RULES,
    PROJECTION_CANDIDATE_LIMIT,
    PROJECTION_CONFIDENCE,
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
    project_mechanisms,
    rule_by_id,
    rule_catalog,
    scope_root_cause_candidate,
)
from smt_quality_agent.ontology import (
    CAUSE_TO_CONCEPT_ID,
    MECHANISMS,
    SCOPE_TO_CONCEPT_ID,
    concept_by_id,
    concept_label,
)


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
        fallback = rule_by_id("rule.fallback_local_printing_state")
        self.assertGreater(spi["confidence_base"], scope["confidence_base"])
        self.assertGreater(scope["confidence_base"], PROJECTION_CONFIDENCE)
        self.assertGreater(PROJECTION_CONFIDENCE, fallback["confidence_base"])

    def test_every_cause_is_registered_vocabulary(self) -> None:
        # cause 文本 = 机理 label(单源),必须能映射回本体;唯一例外是
        # CAUSE_OVERRIDES 里"继续观察"这类兜底措辞。
        for rule in RULES:
            if rule["id"] == "rule.abnormal_observe_next_board":
                continue
            self.assertIn(rule["cause"], CAUSE_TO_CONCEPT_ID, rule["id"])

    def test_hand_written_cause_only_on_exempted_fallbacks(self) -> None:
        for rule in RULES:
            if rule["id"] in CAUSE_OVERRIDES or rule["id"] in RULES_WITHOUT_MECHANISM:
                continue
            self.assertEqual(
                rule["cause"], concept_label(rule["mechanism"]), rule["id"],
            )


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
        # 0.75 × 0.8(跨机种) × 0.9(参数机理典型范围是整板/局部,观测是单Pad)。
        result = diagnose(sample_observation(
            drifted_parameters=["printspeed"], cross_model_baseline=True,
        ))
        drift = next(
            item for item in result["root_cause_assessment"]
            if item["rule_id"] == "rule.parameter_drift"
        )
        self.assertEqual(drift["confidence"], 0.54)
        self.assertEqual(drift["evidence_level"], "中")

    def test_diagnose_never_repeats_a_mechanism_in_top3(self) -> None:
        # 原缺陷:去重按 cause 字符串,同一机理的不同措辞会占掉前 3 的两席。
        result = diagnose(sample_observation(
            trend_kind="gradual", recovery_kind="not_recovered",
            metric_signature={"avdp": "up", "aadp": "up", "ahdp": "flat"},
        ))
        mechanisms = [
            item["mechanism_id"] for item in result["root_cause_assessment"]
            if item["mechanism_id"]
        ]
        self.assertEqual(len(mechanisms), len(set(mechanisms)))
        dedup_reasons = [
            item["reason"] for item in result["decision_trace"]["eliminated"]
        ]
        self.assertIn("同机理去重（保留更高置信候选）", dedup_reasons)

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


class DecisionTraceTest(unittest.TestCase):
    def test_trace_records_every_step_in_order_including_misses(self) -> None:
        result = diagnose(sample_observation())
        steps = result["decision_trace"]["steps"]
        self.assertEqual(len(steps), len(DECISION_RULES))
        self.assertEqual([step["order"] for step in steps],
                         sorted(step["order"] for step in steps))
        spi_gate = next(step for step in steps if step["id"] == "decide.spi_false_alarm_gate")
        self.assertFalse(spi_gate["fired"])
        self.assertEqual(spi_gate["nominated"], [])
        scope_step = next(step for step in steps if step["id"] == "decide.scope_prior")
        self.assertTrue(scope_step["fired"])
        self.assertTrue(scope_step["nominated"][0]["formula"])

    def test_formula_shows_multiplier_breakdown(self) -> None:
        result = diagnose(sample_observation(
            drifted_parameters=["printspeed"], cross_model_baseline=True,
        ))
        drift_step = next(
            step for step in result["decision_trace"]["steps"]
            if step["id"] == "decide.parameter_drift"
        )
        self.assertIn("0.8(跨机种基线)", drift_step["nominated"][0]["formula"])
        self.assertIn("0.9(范围非典型)", drift_step["nominated"][0]["formula"])
        self.assertIn("= 0.54", drift_step["nominated"][0]["formula"])

    def test_eliminated_candidates_carry_reasons(self) -> None:
        result = diagnose(sample_observation())
        nominated_total = sum(
            len(step["nominated"]) for step in result["decision_trace"]["steps"]
        )
        eliminated = result["decision_trace"]["eliminated"]
        self.assertEqual(
            nominated_total,
            len(result["root_cause_assessment"]) + len(eliminated),
        )
        self.assertTrue(all(item["reason"] for item in eliminated))


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
        # 0.8 × 1.15(擦网对齐) × 1.1(单Pad在擦网机理典型范围) = 1.012 → 封顶。
        self.assertEqual(periodic["confidence"], 0.95)
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
        # cause = 机理 label,不再是范围规则自己的措辞。
        self.assertEqual(candidate["cause"], "钢网底部残锡转印")
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
        self.assertTrue(all(item["rule_id"].startswith("rule.projected.") for item in candidates))
        self.assertTrue(all(
            item["rule_source"] == "knowledge_base.mechanism_projection"
            for item in candidates
        ))
        # 少锡×整板的方向精确机理排最前:供锡中断/漏印。
        self.assertEqual(candidates[0]["mechanism_id"], "mech.supply_interruption")
        self.assertIn("供锡", candidates[0]["cause"])

    def test_abnormal_cause_candidates_carry_rule_identity(self) -> None:
        candidates = abnormal_cause_candidates("多锡", "同点多板异常", "高")
        self.assertTrue(candidates)
        self.assertLessEqual(len(candidates), PROJECTION_CANDIDATE_LIMIT)
        self.assertEqual(candidates[0]["rule_id"], "rule.projected.understencil_residue")
        self.assertEqual(candidates[0]["cause"], "钢网底部残锡转印")
        self.assertTrue(candidates[0]["action"])

    def test_abnormal_cause_candidates_fall_back_to_observation(self) -> None:
        # 单点偶发不投影机理——单板孤立一次先复测。
        candidates = abnormal_cause_candidates("多锡", "单点偶发异常", "低")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["rule_id"], "rule.abnormal_observe_next_board")
        self.assertEqual(candidates[0]["cause"], "继续观察")


class MechanismProjectionTest(unittest.TestCase):
    def test_projection_respects_direction(self) -> None:
        for rule in project_mechanisms("少锡", ("spatial.board_wide",)):
            direction = (MECHANISMS[rule["mechanism"]]["properties"])["direction"]
            self.assertIn(direction, {"少锡", "双向"}, rule["mechanism"])

    def test_projection_respects_spatial_coverage(self) -> None:
        for rule in project_mechanisms("多锡", ("spatial.single_pad",)):
            typical = (MECHANISMS[rule["mechanism"]]["properties"])["typical_spatial"]
            self.assertIn("spatial.single_pad", typical, rule["mechanism"])

    def test_projection_ranks_exact_direction_before_bidirectional(self) -> None:
        rules = project_mechanisms("少锡", ("spatial.single_pad",), "temporal.repeated")
        directions = [
            (MECHANISMS[rule["mechanism"]]["properties"])["direction"] for rule in rules
        ]
        exact_seen_after_bidirectional = False
        bidirectional_seen = False
        for direction in directions:
            if direction == "双向":
                bidirectional_seen = True
            elif bidirectional_seen:
                exact_seen_after_bidirectional = True
        self.assertFalse(exact_seen_after_bidirectional)

    def test_projection_is_capped(self) -> None:
        for direction in ("多锡", "少锡"):
            for spatial in ("spatial.single_pad", "spatial.board_wide",
                            "spatial.component_multi_pad", "spatial.local_area"):
                self.assertLessEqual(
                    len(project_mechanisms(direction, (spatial,))),
                    PROJECTION_CANDIDATE_LIMIT,
                )

    def test_projected_candidates_use_mechanism_label_and_action(self) -> None:
        for rule in project_mechanisms("多锡", ("spatial.board_wide",)):
            mechanism = MECHANISMS[rule["mechanism"]]
            self.assertEqual(rule["cause"], mechanism["label"])
            self.assertEqual(rule["action"], mechanism["properties"]["action"])


class SpatialTypicalityTest(unittest.TestCase):
    def test_typical_spatial_boosts_candidate(self) -> None:
        result = diagnose(sample_observation(spatial="spatial.single_pad"))
        scope = next(
            item for item in result["root_cause_assessment"]
            if item["rule_id"] == "rule.over_volume_single_pad"
        )
        # 残锡转印典型范围含单Pad:0.7 × 1.1 = 0.77。
        self.assertEqual(scope["confidence"], 0.77)
        self.assertIn("1.1(范围典型)", scope["confidence_formula"])

    def test_no_observation_spatial_means_no_adjustment(self) -> None:
        candidate = scope_root_cause_candidate("多锡", "单Pad孤立异常", "detail")
        self.assertEqual(candidate["confidence"], candidate["confidence_base"])


class RuleCatalogTest(unittest.TestCase):
    def test_rule_catalog_exposes_reviewable_rules(self) -> None:
        catalog = rule_catalog()
        self.assertEqual(catalog["version"], "rule-catalog-v6")
        self.assertGreater(catalog["rule_count"], 20)
        self.assertEqual(catalog["rule_count"], len(catalog["rules"]))
        rule_ids = [rule["rule_id"] for rule in catalog["rules"]]
        self.assertEqual(len(rule_ids), len(set(rule_ids)))
        self.assertIn("rule.over_volume_single_pad", rule_ids)
        self.assertIn("rule.insufficient_volume_board_same_direction", rule_ids)
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
        self.assertTrue(clogging["action"])
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

    def test_hand_written_projection_tables_are_gone(self) -> None:
        # 事件/实时候选由机理目录投影生成,目录里不允许再出现手工格子规则;
        # 唯一保留的是实时"继续观察"兜底。
        catalog = rule_catalog()
        legacy = [
            rule["rule_id"] for rule in catalog["rules"]
            if (rule["rule_id"].startswith("rule.event_")
                or rule["rule_id"].startswith("rule.abnormal_"))
            and rule["rule_id"] != "rule.abnormal_observe_next_board"
        ]
        self.assertEqual(legacy, [])
        # 决策梯里必须能看到投影这一级。
        self.assertIn("decide.mechanism_projection",
                      [rule["rule_id"] for rule in catalog["rules"]])

    def test_squeegee_content_does_not_guess_adjustment_direction(self) -> None:
        # 刮刀相关知识(机理规范动作 + 刮刀参数漂移规则)不预设调压方向,
        # 必须现场确认后再调。
        squeegee_mech = next(
            item for item in rule_catalog()["mechanisms"]
            if item["mechanism_id"] == "mech.squeegee_one_side"
        )
        drift_rule = rule_by_id("rule.squeegee_parameter_drift")
        for text in (squeegee_mech["action"], drift_rule["action_template"]):
            self.assertIn("现场", text)
            self.assertNotIn("适当提高", text)
            self.assertNotIn("适当降低", text)


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

    def test_disposition_vocabulary_is_ontology_backed(self) -> None:
        # 处置文本与优先级取自本体 Disposition 概念,不再平行维护。
        for rule in DISPOSITION_RULES:
            concept = concept_by_id(rule["concept"])
            self.assertIsNotNone(concept, rule["id"])
            self.assertEqual(rule["disposition"], concept["label"])
            self.assertEqual(rule["priority"], concept["properties"]["priority"])


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
