import json

from anthropic import Anthropic

from pdftranslator.ai.base import AbstractTranslator, TextBlock, TranslatedBlock
from pdftranslator.config import Config
from pdftranslator.logging_config import get_logger

_log = get_logger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_MAX_TOKENS = 4096


class AnthropicTranslator(AbstractTranslator):
    def __init__(self) -> None:
        api_key = Config.anthropic_api_key()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        _log.info("AnthropicTranslator created (model=%s)", ANTHROPIC_MODEL)
        self._client = Anthropic(api_key=api_key)

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

        _log.debug("Anthropic: translating %d blocks (%d chars)", len(blocks), len(blocks_json))
        response = self._client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            temperature=0.3,
            system=(
                f"You are a professional PDF translator. "
                f"Translate text blocks from {source_lang} to {target_lang}. "
                f"Return ONLY valid JSON. Keep translations concise. "
                f"Context before: {context_before or '(none)'}. "
                f"Context after: {context_after or '(none)'}."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Translate these blocks. Return JSON with format: "
                        '{"blocks": [{"id": 0, "translated": "..."}]}. '
                        f"Blocks:\n{blocks_json}"
                    ),
                }
            ],
        )

        content = response.content[0].text
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            _log.warning("Anthropic returned invalid JSON, trying to recover: %s", content[:200])
            import re
            data = {"blocks": []}
            for m in re.finditer(r'"id":\s*(\d+).*?"translated":\s*"((?:[^"\\]|\\.)*)"', content):
                data["blocks"].append({"id": int(m.group(1)), "translated": m.group(2)})
            if not data["blocks"]:
                raise ValueError(f"Could not recover translations from AI response: {content[:300]}")
        _log.debug("Anthropic: got %d translations, tokens: in=%d out=%d",
                    len(data["blocks"]),
                    response.usage.input_tokens if response.usage else 0,
                    response.usage.output_tokens if response.usage else 0)

        block_by_id = {b.id: b for b in blocks}
        result: list[TranslatedBlock] = []
        for item in data["blocks"]:
            bid = item["id"]
            if bid not in block_by_id:
                _log.warning("Anthropic returned unknown block id=%d, skipping", bid)
                continue
            result.append(TranslatedBlock(
                id=bid,
                original_text=block_by_id[bid].text,
                translated_text=item["translated"],
            ))
        return result

    def estimate_cost(
        self,
        blocks: list[TextBlock],
        source_lang: str,
        target_lang: str,
    ) -> float:
        total_chars = sum(len(b.text) for b in blocks)
        estimated_input = total_chars * 1.3
        estimated_output = total_chars * 1.5
        price_per_1m_input = 3.0
        price_per_1m_output = 15.0
        return (estimated_input / 1_000_000) * price_per_1m_input + (
            estimated_output / 1_000_000
        ) * price_per_1m_output

    @staticmethod
    def validate_key(key: str) -> tuple[bool, str]:
        if not key.strip():
            return False, "API key is empty"
        try:
            client = Anthropic(api_key=key.strip())
            client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return True, "Anthropic API key is valid"
        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "unauthorized" in msg.lower() or "auth" in msg.lower():
                return False, "Invalid API key"
            if "403" in msg or "forbidden" in msg.lower():
                return False, "API key lacks permissions"
            if "429" in msg:
                return False, "Rate limited — try again later"
            return False, f"Connection failed: {msg[:120]}"
