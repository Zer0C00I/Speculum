import json

from openai import OpenAI

from pdftranslator.ai.base import AbstractTranslator, TextBlock, TranslatedBlock
from pdftranslator.config import Config
from pdftranslator.logging_config import get_logger

_log = get_logger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

TRANSLATION_SYSTEM_PROMPT = """You are a professional PDF translator. Translate text blocks from {source_lang} to {target_lang}.

Rules:
- Return ONLY valid JSON, no other text.
- Preserve the meaning and tone of the original.
- Keep translations concise — they must fit into the same space as the original text.
- If a block contains formatting codes or numbers, preserve them exactly.
- For technical terms, use standard {target_lang} equivalents.

Context before this batch: {context_before}
Context after this batch: {context_after}

Return format:
{{"blocks": [{{"id": 0, "translated": "..."}}, {{"id": 1, "translated": "..."}}]}}"""


class DeepSeekTranslator(AbstractTranslator):
    def __init__(self) -> None:
        api_key = Config.deepseek_api_key()
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY not set in .env")
        _log.info("DeepSeekTranslator created (model=%s)", DEEPSEEK_MODEL)
        self._client = OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    def translate_batch(
        self,
        blocks: list[TextBlock],
        source_lang: str,
        target_lang: str,
        context_before: str = "",
        context_after: str = "",
    ) -> list[TranslatedBlock]:
        blocks_json = json.dumps(
            [{"id": b.id, "text": b.text} for b in blocks],
            ensure_ascii=False,
        )

        system = TRANSLATION_SYSTEM_PROMPT.format(
            source_lang=source_lang,
            target_lang=target_lang,
            context_before=context_before or "(none)",
            context_after=context_after or "(none)",
        )

        _log.debug("DeepSeek: translating %d blocks (%d chars)", len(blocks), len(blocks_json))
        response = self._client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": blocks_json},
            ],
            temperature=0.3,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        assert content is not None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            _log.warning("DeepSeek returned invalid JSON, trying to recover: %s", content[:200])
            import re
            data = {"blocks": []}
            for m in re.finditer(r'"id":\s*(\d+).*?"translated":\s*"((?:[^"\\]|\\.)*)"', content):
                data["blocks"].append({"id": int(m.group(1)), "translated": m.group(2)})
            if not data["blocks"]:
                raise ValueError(f"Could not recover translations from AI response: {content[:300]}")
        _log.debug("DeepSeek: got %d translations, tokens: in=%d out=%d",
                    len(data["blocks"]),
                    response.usage.prompt_tokens if response.usage else 0,
                    response.usage.completion_tokens if response.usage else 0)

        block_by_id = {b.id: b for b in blocks}
        result: list[TranslatedBlock] = []
        for item in data["blocks"]:
            bid = item["id"]
            if bid not in block_by_id:
                _log.warning("DeepSeek returned unknown block id=%d, skipping", bid)
                continue
            translated_text = item.get("translated") or item.get("translation") or item.get("text", "")
            if not translated_text:
                _log.warning("DeepSeek block id=%d has no translated text, skipping", bid)
                continue
            result.append(TranslatedBlock(
                id=bid,
                original_text=block_by_id[bid].text,
                translated_text=translated_text,
            ))
        return result

    def estimate_cost(
        self,
        blocks: list[TextBlock],
        source_lang: str,
        target_lang: str,
    ) -> float:
        total_chars = sum(len(b.text) for b in blocks)
        estimated_tokens = total_chars * 1.3
        price_per_1m_input = 0.27
        price_per_1m_output = 1.10
        return (estimated_tokens / 1_000_000) * (price_per_1m_input + price_per_1m_output)

    @staticmethod
    def validate_key(key: str) -> tuple[bool, str]:
        if not key.strip():
            return False, "API key is empty"
        try:
            client = OpenAI(api_key=key.strip(), base_url=DEEPSEEK_BASE_URL)
            client.models.list()
            return True, "DeepSeek API key is valid"
        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "unauthorized" in msg.lower() or "auth" in msg.lower():
                return False, "Invalid API key"
            if "403" in msg or "forbidden" in msg.lower():
                return False, "API key lacks permissions"
            if "429" in msg:
                return False, "Rate limited — try again later"
            return False, f"Connection failed: {msg[:120]}"
