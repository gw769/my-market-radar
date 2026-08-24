import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from app.services.marketplace import ai


class MarketplaceAITests(unittest.TestCase):
    def settings(self):
        return SimpleNamespace(
            LLM_PROVIDER="openai_compatible",
            LLM_API_KEY="secret-test-key",
            LLM_BASE_URL="https://sub2api.example.test",
            LLM_MODEL="gpt-5.6-sol",
            LLM_REASONING_EFFORT="low",
            LLM_TIMEOUT_SECONDS=20.0,
            LLM_MAX_RETRIES=5,
        )

    def test_towel_uses_deterministic_translation_without_network(self):
        with patch.object(ai, "_chat_json") as chat:
            result = ai.translate_keyword("毛巾")
        chat.assert_not_called()
        self.assertEqual(result["search_term"], "towel")
        self.assertIn("tuala", result["aliases"])
        self.assertEqual(result["source"], "deterministic")

    def test_ai_translation_is_validated_and_normalized(self):
        with patch.object(
            ai,
            "_chat_json",
            return_value={
                "search_term": "Kids Insulated Water Bottle",
                "aliases": ["Botol Air Berpenebat Kanak-kanak", "kids insulated water bottle"],
            },
        ):
            result = ai.translate_keyword("儿童保温杯")
        self.assertEqual(result["search_term"], "kids insulated water bottle")
        self.assertEqual(result["source"], "ai")
        self.assertEqual(result["model"], ai.get_settings().LLM_MODEL)

    def test_chat_json_never_returns_or_logs_api_key(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"value": "ok"})}}]
        }
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.return_value = response
        schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        with patch.object(ai, "get_settings", return_value=self.settings()), patch.object(
            ai.httpx, "Client", return_value=client
        ):
            result = ai._chat_json(
                system="system",
                user="user",
                schema_name="test_schema",
                schema=schema,
                max_completion_tokens=50,
            )
        self.assertEqual(result, {"value": "ok"})
        headers = client.post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-test-key")
        self.assertNotIn("secret-test-key", json.dumps(result))

    def test_chat_json_retries_timeout_five_times_then_succeeds(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({"value": "ok"})}}]
        }
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.side_effect = [
            httpx.ReadTimeout("temporary timeout") for _ in range(5)
        ] + [response]
        callback = Mock()
        schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }

        with patch.object(ai, "get_settings", return_value=self.settings()), patch.object(
            ai.httpx, "Client", return_value=client
        ), patch.object(ai.time, "sleep") as sleep:
            result = ai._chat_json(
                system="system",
                user="user",
                schema_name="test_schema",
                schema=schema,
                max_completion_tokens=50,
                on_retry=callback,
            )

        self.assertEqual(result, {"value": "ok"})
        self.assertEqual(client.post.call_count, 6)
        self.assertEqual(sleep.call_count, 5)
        self.assertEqual(
            [call.args[:2] for call in callback.call_args_list],
            [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)],
        )

    def test_chat_json_does_not_retry_non_transient_auth_error(self):
        response = Mock(status_code=401)
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.post.return_value = response
        schema = {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }

        with patch.object(ai, "get_settings", return_value=self.settings()), patch.object(
            ai.httpx, "Client", return_value=client
        ), patch.object(ai.time, "sleep") as sleep:
            with self.assertRaisesRegex(ai.MarketplaceAIError, "HTTP 401"):
                ai._chat_json(
                    system="system",
                    user="user",
                    schema_name="test_schema",
                    schema=schema,
                    max_completion_tokens=50,
                )

        self.assertEqual(client.post.call_count, 1)
        sleep.assert_not_called()

    def test_ai_insight_rejects_numbers_not_in_evidence(self):
        analysis = {
            "keyword": "毛巾",
            "opportunity_score": 64.6,
            "verdict": "谨慎观察",
            "confidence": 85.4,
            "evidence": {"grade": "A", "sample_total": 107},
            "platform_scores": {},
            "opportunity_segments": [],
        }
        with patch.object(
            ai,
            "_chat_json",
            return_value={
                "summary": "机会分 99，适合进入。",
                "findings": [],
                "risks": [],
                "actions": ["先验证供应链。"],
                "next_steps": [
                    {
                        "stage": stage,
                        "title": "核验商品",
                        "why": "需要确认公开样本。",
                        "task": "检查商品页面并记录缺失字段。",
                        "watch": "观察公开字段是否完整。",
                    }
                    for stage in ("先核验", "小规模测试", "持续复盘")
                ],
            },
        ):
            with self.assertRaises(ai.MarketplaceAIError):
                ai.generate_market_insights(analysis)

    def test_ai_insight_returns_structured_next_steps_without_changing_score(self):
        analysis = {
            "keyword": "毛巾",
            "opportunity_score": 66.5,
            "verdict": "谨慎观察",
            "confidence": 85.6,
            "evidence": {"grade": "A", "sample_total": 111, "collector_health": 99.3},
            "platform_scores": {},
            "opportunity_segments": [],
            "recommendations": ["先核验价格带。"],
        }
        raw_steps = [
            {
                "stage": stage,
                "title": title,
                "why": "当前公开证据支持继续验证。",
                "task": "复核目标商品并记录真实成本。",
                "watch": "观察成本与公开信号是否一致。",
            }
            for stage, title in (
                ("先核验", "核验搜索结果"),
                ("小规模测试", "验证商品定位"),
                ("持续复盘", "复盘新增证据"),
            )
        ]
        with patch.object(
            ai,
            "_chat_json",
            return_value={
                "summary": "机会分为 66.5，当前应谨慎观察。",
                "findings": ["证据等级为 A。"],
                "risks": ["真实成本仍缺失。"],
                "next_steps": raw_steps,
            },
        ) as chat:
            result = ai.generate_market_insights(analysis)

        step_schema = chat.call_args.kwargs["schema"]["properties"]["next_steps"]["items"]
        self.assertEqual(step_schema["properties"]["task"]["pattern"], "^[^0-9]*$")
        self.assertEqual(len(result["next_steps"]), 3)
        self.assertEqual(result["next_steps"][0]["tasks"], [raw_steps[0]["task"]])
        self.assertEqual(result["actions"], [step["task"] for step in raw_steps])
        self.assertFalse(result["score_changed"])
        self.assertEqual(analysis["opportunity_score"], 66.5)


if __name__ == "__main__":
    unittest.main()
