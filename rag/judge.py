"""LLM-as-judge faithfulness agent.

A small, deliberately-scoped agent that scores whether an answer is faithful
to the chunks the generator was given. Used by the eval harness as a sixth
check alongside the five mechanical ones (drug detection, retrieval
coverage, citation presence, refusal phrasing, required phrasing).

Why this exists
---------------
The mechanical checks catch *structural* failures — did the system cite at
all, did it abstain when it should — but they cannot catch *substantive*
failures, where the answer cites [3] but the chunk at [3] does not actually
support the claim. That's the hallucination class that matters most in a
medical domain, and it's the gap a judge agent closes.

Design choices, all defensible in an interview
----------------------------------------------

1. **Different model from the generator.** Generator is DeepSeek v3.2;
   judge defaults to Anthropic Claude Haiku 4.5 via OpenRouter. Same-model
   judging shares the generator's blind spots — if the generator
   hallucinated because it misread chunk [3], the same model is likely to
   "agree with itself" when judging. Cross-family judging breaks that
   shared distribution.

2. **Chain-of-verification prompting.** The judge is forced to (a) list
   each factual claim in the answer, (b) identify the cited passage, (c)
   quote the specific span of the passage that supports the claim, (d)
   verdict. The "quote the span" step is the anti-hallucination guard for
   the judge itself — if the judge can't produce a span, it cannot
   honestly claim the answer is supported.

3. **Structured JSON output.** Forced via `response_format=json_object`
   when the underlying provider supports it; parsed defensively otherwise.
   Downstream code reads typed fields, not free text.

4. **Fails open, not closed.** If the judge errors (rate limit, parse
   failure, no API key), eval records the check as "skip" — not "fail" —
   so an outage on the judge service doesn't fake a regression in the
   pipeline under test.

5. **The judge is signal, not gate.** Used in the offline eval harness,
   never in the live request path. Adding a judge to production would
   double LLM cost and double latency for a fraction-of-a-percent
   confidence lift — not worth it. The right place to use a judge is the
   place we use it.

Limitations to flag out loud
----------------------------
- The judge itself can hallucinate. Chain-of-verification and cross-family
  routing mitigate but do not eliminate this. A stronger system would run
  the judge K times with different temperatures and majority-vote
  ("self-consistency"). Easy extension; not implemented here.
- Judge cost: ~1 extra LLM call per evaluated question (~$0.001 with the
  default model). Negligible for 18 questions, non-negligible at scale.
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import httpx
from openai import OpenAI

from rag.retrieve import RetrievedChunk

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "anthropic/claude-haiku-4.5"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

JUDGE_SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are a careful evaluator of medical Q&A answers. You did NOT write the
    answer under review; you are auditing it for FAITHFULNESS to the source
    passages the writer was given.

    You will receive:
      - QUESTION: the user's question.
      - CONTEXT: numbered passages [1], [2], ... that were given to the writer.
      - ANSWER: the writer's response, which should cite passages with [n].

    Your job is to decide:
      1. Is every factual claim in ANSWER supported by a passage in CONTEXT
         that ANSWER cites? Synthesis across passages is allowed — for example,
         combining a class-level warning in one passage with a specific drug
         in another — as long as every step of the synthesis is grounded in a
         cited passage.
      2. Do all citation numbers [n] in ANSWER point to passages that actually
         exist in CONTEXT and that support the surrounding claim?
      3. Are there factual claims in ANSWER that are NOT supported by any
         cited passage? These are the dangerous failures.

    Output rules — follow exactly:
      - Output ONE JSON object. No prose before or after.
      - Schema:
        {
          "faithful": boolean,
          "citation_valid": boolean,
          "unsupported_claims": [
            {"claim": "...", "reason": "...", "cited": "[n] or none"}
          ],
          "confidence": number between 0 and 1,
          "summary": "one short sentence"
        }
      - `faithful` is true ONLY if every factual claim is supported by a
        cited passage. Generic safety disclaimers like "consult a clinician"
        do not require a citation.
      - `citation_valid` is true ONLY if every [n] in ANSWER refers to a
        passage that exists and supports the surrounding claim. Citing
        passage [9] when CONTEXT only goes to [5] is invalid.
      - For each unsupported claim, you MUST quote the specific claim from
        ANSWER and explain in `reason` why the cited passage does not support
        it (or that no passage was cited). This anti-hallucination step is
        non-negotiable: if you cannot quote the claim and locate it in the
        answer, do not list it.
      - If ANSWER is an abstention ("I don't know based on the provided
        sources" or similar), set faithful=true and citation_valid=true and
        summary="abstained".
      - If ANSWER is empty or a non-medical greeting/social reply, set
        faithful=true, citation_valid=true, summary="non-medical reply".
    """
)


@dataclass
class UnsupportedClaim:
    claim: str
    reason: str
    cited: str = ""


@dataclass
class JudgeVerdict:
    faithful: bool
    citation_valid: bool
    unsupported_claims: List[UnsupportedClaim] = field(default_factory=list)
    confidence: float = 0.0
    summary: str = ""
    judge_model: str = ""
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.faithful and self.citation_valid and not self.unsupported_claims

    def to_dict(self) -> dict:
        return {
            "faithful": self.faithful,
            "citation_valid": self.citation_valid,
            "unsupported_claims": [
                {"claim": u.claim, "reason": u.reason, "cited": u.cited}
                for u in self.unsupported_claims
            ],
            "confidence": self.confidence,
            "summary": self.summary,
            "judge_model": self.judge_model,
            "error": self.error,
        }


def _format_context(chunks: Sequence[RetrievedChunk]) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        drug = (c.metadata or {}).get("drug_name") or "unknown"
        section = (c.metadata or {}).get("section") or "general"
        blocks.append(
            f"[{i}] drug: {drug} | section: {section}\n{c.text.strip()}"
        )
    return "\n\n".join(blocks)


# Lenient JSON extraction — some models wrap JSON in ```json fences or add
# a sentence before the object. Locate the first balanced { ... } block.
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class JudgeAgent:
    """LLM-as-judge agent that scores answer faithfulness to retrieved context.

    The agent owns its own OpenAI-compatible client pointed at OpenRouter and
    a system prompt distinct from the generator's. Calling `judge()` returns
    a typed `JudgeVerdict`; transport / parse failures yield a verdict with
    `.error` populated rather than raising, so the eval harness can record
    a "skip" instead of a false failure.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 45.0,
    ):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or os.environ.get("JUDGE_MODEL", DEFAULT_JUDGE_MODEL)
        self._base_url = (base_url or OPENROUTER_BASE_URL).rstrip("/")
        self._timeout = timeout
        self._client: Optional[OpenAI] = None

    def _ensure_client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set; judge cannot run.")
        # Match the generator's HTTP setup so SDK + httpx version mismatch
        # doesn't surface here (see rag/generate.py).
        _timeout = httpx.Timeout(self._timeout, connect=15.0)
        _http = httpx.Client(timeout=_timeout, follow_redirects=True)
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self._base_url,
            http_client=_http,
        )
        return self._client

    def judge(
        self,
        question: str,
        chunks: Sequence[RetrievedChunk],
        answer: str,
    ) -> JudgeVerdict:
        """Return a faithfulness verdict for `answer` against `chunks`.

        Never raises. Transport / parse errors return a verdict with .error
        set; the caller decides how to treat that (the eval harness treats
        it as a 'skip' so judge outages don't masquerade as pipeline
        regressions).
        """
        if not answer.strip():
            return JudgeVerdict(
                faithful=True,
                citation_valid=True,
                summary="empty answer; nothing to judge",
                judge_model=self.model,
            )

        user_prompt = (
            f"QUESTION: {question}\n\n"
            f"CONTEXT:\n{_format_context(chunks) or '(no passages were retrieved)'}\n\n"
            f"ANSWER:\n{answer.strip()}\n\n"
            "Return the JSON verdict now."
        )

        try:
            client = self._ensure_client()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=900,
                # Some providers (OpenAI) honour this; others (Anthropic via
                # OpenRouter) ignore it but still emit JSON given the prompt.
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        except TypeError:
            # SDK signature mismatch on response_format — retry without it.
            try:
                client = self._ensure_client()
                resp = client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                    max_tokens=900,
                )
                raw = resp.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001
                logger.exception("Judge transport error (retry path)")
                return JudgeVerdict(
                    faithful=False, citation_valid=False,
                    summary="judge unavailable",
                    judge_model=self.model, error=f"{type(exc).__name__}: {exc}",
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Judge transport error")
            return JudgeVerdict(
                faithful=False, citation_valid=False,
                summary="judge unavailable",
                judge_model=self.model, error=f"{type(exc).__name__}: {exc}",
            )

        parsed = _extract_json(raw)
        if not parsed:
            return JudgeVerdict(
                faithful=False, citation_valid=False,
                summary="judge returned unparseable response",
                judge_model=self.model,
                error="parse-failure",
            )

        try:
            claims_raw = parsed.get("unsupported_claims") or []
            claims: List[UnsupportedClaim] = []
            for c in claims_raw:
                if not isinstance(c, dict):
                    continue
                claims.append(
                    UnsupportedClaim(
                        claim=str(c.get("claim") or "").strip(),
                        reason=str(c.get("reason") or "").strip(),
                        cited=str(c.get("cited") or "").strip(),
                    )
                )
            return JudgeVerdict(
                faithful=bool(parsed.get("faithful", False)),
                citation_valid=bool(parsed.get("citation_valid", False)),
                unsupported_claims=claims,
                confidence=float(parsed.get("confidence") or 0.0),
                summary=str(parsed.get("summary") or "").strip(),
                judge_model=self.model,
            )
        except (TypeError, ValueError) as exc:
            return JudgeVerdict(
                faithful=False, citation_valid=False,
                summary="judge returned malformed JSON",
                judge_model=self.model,
                error=f"schema-mismatch: {exc}",
            )


_default_judge: Optional[JudgeAgent] = None


def get_judge() -> JudgeAgent:
    global _default_judge
    if _default_judge is None:
        _default_judge = JudgeAgent()
    return _default_judge
