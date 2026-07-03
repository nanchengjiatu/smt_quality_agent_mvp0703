import unittest

from smt_quality_agent.drilldown_chat import build_rule_chat_response, classify_chat_intent


def sample_trigger() -> dict:
    return {
        "trigger_id": "TRG001",
        "parameter_check": {
            "verdict": "印刷参数未发现明显偏离。",
            "drifted": [],
        },
        "param_events": [],
        "analysis_contract": {
            "version": "analysis-contract-v2",
            "trigger": {
                "trigger_id": "TRG001",
                "pad_name": "C1_1",
                "conclusion": "焊盘 C1_1 连续 3 块生产板判 Over Volume（多锡），Agent 判定为单Pad孤立异常。",
            },
            "trend": {"kind": "step", "verdict": "突变型（无事前爬升）", "detail": "触发段均值跳升。"},
            "scope": {
                "category": "单Pad孤立异常",
                "detail": "同元件和全量窗口没有显示明显扩散。",
                "confidence": "中",
            },
            "evidence": {
                "summary": [
                    {"name": "连续触发", "value": "3 块生产板", "detail": "复测记录不参与计数。"},
                ],
                "context": {
                    "total_rows": 108,
                    "ng_rows": 3,
                    "same_component_ng_rows": 3,
                    "same_pad_ng_rows": 3,
                },
                "exclusion_checks": [
                    {"name": "数据连续性", "status": "pass", "detail": "连续性检查未发现明显数据问题。"},
                    {"name": "SPI 假异常", "status": "pass", "detail": "未发现明显 SPI 假异常信号。"},
                ],
                "tags": ["范围：单Pad孤立异常"],
            },
            "root_cause_candidates": [
                {
                    "cause": "钢网单孔底部残锡或开口异常",
                    "evidence": "异常局限于单 Pad。",
                    "action": "停机检查该 Pad 对应钢网孔。",
                },
            ],
            "disposition": {
                "suggestion": "按单点异常做快速确认",
                "reason": "当前证据更像局部单点问题。",
                "primary_action": "检查对应钢网孔底部残锡。",
            },
            "recheck": {
                "recovery_kind": "recovered",
                "recovery_verdict": "已恢复",
                "recovery_detail": "异常自第 1 块后续板起消失。",
                "criteria": ["连续复判 3 块生产板该 Pad 不再 NG。"],
            },
        },
    }


class DrilldownChatTest(unittest.TestCase):
    def test_scope_question_returns_three_part_answer(self) -> None:
        response = build_rule_chat_response(sample_trigger(), "为什么判定为这个范围？")
        self.assertEqual(response["intent"], "scope")
        self.assertIn("单Pad孤立异常", response["answer"]["conclusion"])
        self.assertTrue(response["answer"]["evidence"])
        self.assertTrue(response["answer"]["next_step"])

    def test_scope_next_step_is_category_specific(self) -> None:
        response = build_rule_chat_response(sample_trigger(), "为什么判定为这个范围？")
        self.assertIn("钢网单孔", response["answer"]["next_step"])

        board_trigger = sample_trigger()
        board_trigger["analysis_contract"]["scope"]["category"] = "整板同向"
        response = build_rule_chat_response(board_trigger, "为什么判定为这个范围？")
        self.assertIn("整板", response["answer"]["next_step"])

    def test_action_question_uses_disposition(self) -> None:
        response = build_rule_chat_response(sample_trigger(), "现场先查什么？")
        self.assertEqual(response["intent"], "actions")
        self.assertIn("按单点异常", response["answer"]["conclusion"])
        self.assertIn("钢网孔", response["answer"]["next_step"])

    def test_spi_false_alarm_question_uses_exclusion_check(self) -> None:
        response = build_rule_chat_response(sample_trigger(), "这是不是 SPI 假异常？")
        self.assertEqual(response["intent"], "spi_false_alarm")
        self.assertIn("没有明显 SPI 假异常", response["answer"]["conclusion"])

    def test_evidence_question_quotes_primary_candidate(self) -> None:
        response = build_rule_chat_response(sample_trigger(), "哪些证据支持首要根因？")
        self.assertEqual(response["intent"], "evidence")
        self.assertIn("钢网单孔底部残锡", response["answer"]["conclusion"])

    def test_intent_classification(self) -> None:
        self.assertEqual(classify_chat_intent("为什么判定为这个范围？"), "scope")
        self.assertEqual(classify_chat_intent("这是不是 SPI 假异常？"), "spi_false_alarm")
        self.assertEqual(classify_chat_intent("解释参数对比结果"), "parameters")
        self.assertEqual(classify_chat_intent("现场先查什么？"), "actions")


if __name__ == "__main__":
    unittest.main()
