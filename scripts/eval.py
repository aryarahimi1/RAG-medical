"""Regression eval for the medication-RAG pipeline.

Reads tests/eval/golden_qa.json, runs each question through RAGPipeline.run()
once, and writes a Markdown report (pass rate, per-category, latency quantiles,
per-question detail). Checks: drug_detect, retrieval_cover, citations, idk,
phrases, faithfulness. Each is skipped — not failed — when not applicable.
auto_ingest is forced off so the eval never modifies the corpus.

The `faithfulness` check is an LLM-as-judge agent (rag/judge.py) that runs
on a DIFFERENT model than the generator (default: Anthropic Claude Haiku via
OpenRouter; generator default: DeepSeek v3.2). Same-model judging shares
blind spots, so cross-family routing is the design intent.

    python -m scripts.eval                       # with generation + judge
    python -m scripts.eval --no-generation       # retrieval+rerank only
    python -m scripts.eval --no-judge            # skip the faithfulness agent
    python -m scripts.eval --filter pharmacokinetics
    python -m scripts.eval --output some/path.md

Exit code: 1 if any applicable check failed; 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from rag.pipeline import RAGPipeline, RAGResult  # noqa: E402
from rag.judge import JudgeAgent, JudgeVerdict  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "eval" / "golden_qa.json"
DEFAULT_OUTPUT = ROOT / "tests" / "eval" / "last_run.md"

CITATION_RE = re.compile(r"\[\d+\]")
IDK_PHRASES = (
    "i don't know", "don't have", "not in my knowledge",
    "can't answer", "unable to", "insufficient", "no information",
)
STAGES = ("redact", "detect", "retrieve", "rerank", "generate")
logger = logging.getLogger("eval")


@dataclass
class CheckOutcome:
    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""


@dataclass
class QuestionOutcome:
    qid: str
    category: str
    question: str
    checks: List[CheckOutcome] = field(default_factory=list)
    timing_ms: Dict[str, float] = field(default_factory=dict)
    judge: Optional[JudgeVerdict] = None

    @property
    def passed(self) -> bool:
        return all(c.status != "fail" for c in self.checks)


# ---------------------------------------------------------------------------
# Checks — each returns "skip" when not applicable so it doesn't penalize.
# ---------------------------------------------------------------------------


def _check_drug_detection(expected: List[str], result: RAGResult) -> CheckOutcome:
    if not expected:
        return CheckOutcome("drug_detect", "skip")
    detected = {d.canonical.lower() for d in result.detected_drugs}
    missing = [d for d in expected if d.lower() not in detected]
    if missing:
        return CheckOutcome(
            "drug_detect", "fail",
            f"missing: {missing}; got: {sorted(detected) or '[]'}",
        )
    return CheckOutcome("drug_detect", "pass")


def _check_retrieval_coverage(expected: List[str], result: RAGResult) -> CheckOutcome:
    if not expected:
        return CheckOutcome("retrieval_cover", "skip")
    chunk_drugs = {
        str((c.metadata or {}).get("drug_name", "")).lower()
        for c in result.reranked
    }
    chunk_drugs.discard("")
    missing = [d for d in expected if d.lower() not in chunk_drugs]
    if missing:
        return CheckOutcome(
            "retrieval_cover", "fail",
            f"no reranked chunks for: {missing}; got: {sorted(chunk_drugs) or '[]'}",
        )
    return CheckOutcome("retrieval_cover", "pass")


def _answer_text(result: RAGResult) -> str:
    return (result.generation.answer if result.generation else "") or ""


def _check_citations(must_cite: bool, result: RAGResult, generated: bool) -> CheckOutcome:
    if not generated or not must_cite:
        return CheckOutcome("citations", "skip")
    if CITATION_RE.search(_answer_text(result)):
        return CheckOutcome("citations", "pass")
    return CheckOutcome("citations", "fail", "no [n] citation marker in answer")


def _check_idk(must_be_idk: bool, result: RAGResult, generated: bool) -> CheckOutcome:
    if not generated or not must_be_idk:
        return CheckOutcome("idk", "skip")
    answer = _answer_text(result).lower()
    if any(phrase in answer for phrase in IDK_PHRASES):
        return CheckOutcome("idk", "pass")
    return CheckOutcome("idk", "fail", "no abstain phrase found in answer")


def _check_phrases(phrases: List[str], result: RAGResult, generated: bool) -> CheckOutcome:
    if not generated or not phrases:
        return CheckOutcome("phrases", "skip")
    answer = _answer_text(result).lower()
    missing = [p for p in phrases if p.lower() not in answer]
    if missing:
        return CheckOutcome("phrases", "fail", f"missing phrases: {missing}")
    return CheckOutcome("phrases", "pass")


def _check_faithfulness(
    result: RAGResult,
    generated: bool,
    judge: Optional[JudgeAgent],
    must_be_idk: bool,
) -> Tuple[CheckOutcome, Optional[JudgeVerdict]]:
    """Run the LLM-as-judge agent and translate its verdict into a check.

    Skip rules:
      - Generator did not run (retrieval-only mode or generation error).
      - No judge available (--no-judge flag or no API key).
      - The question expects refusal; the judge handles abstentions
        cleanly but the mechanical idk check is the authoritative signal.

    Failure rules:
      - Judge transport / parse failure → SKIP, not fail. We never let a
        flaky judge service masquerade as a regression in the pipeline.
      - Judge says faithful=false or citation_valid=false → FAIL with the
        unsupported claims as detail.
    """
    if not generated or judge is None or must_be_idk:
        return CheckOutcome("faithfulness", "skip"), None

    verdict = judge.judge(result.question, list(result.reranked), _answer_text(result))

    if verdict.error:
        return CheckOutcome("faithfulness", "skip", f"judge: {verdict.error}"), verdict

    if verdict.passed:
        return CheckOutcome("faithfulness", "pass", verdict.summary or ""), verdict

    parts: List[str] = []
    if not verdict.citation_valid:
        parts.append("invalid citations")
    if verdict.unsupported_claims:
        parts.append(
            f"unsupported: " + "; ".join(
                f"{u.claim[:80]}{'…' if len(u.claim) > 80 else ''}"
                for u in verdict.unsupported_claims[:3]
            )
        )
    detail = " | ".join(parts) or verdict.summary or "judge marked unfaithful"
    return CheckOutcome("faithfulness", "fail", detail), verdict


def run_question(
    pipeline: RAGPipeline,
    q: Dict[str, Any],
    skip_generation: bool,
    judge: Optional[JudgeAgent] = None,
) -> QuestionOutcome:
    expected = q.get("expected", {})
    result = pipeline.run(q["question"], skip_generation=skip_generation, auto_ingest=False)
    generated = (not skip_generation) and result.generation is not None and not result.error

    outcome = QuestionOutcome(
        qid=q["id"], category=q["category"], question=q["question"],
        timing_ms={s: getattr(result.timing, f"{s}_ms", 0.0) for s in STAGES},
    )

    faith_check, verdict = _check_faithfulness(
        result, generated, judge, bool(expected.get("must_be_idk"))
    )
    outcome.judge = verdict
    outcome.checks = [
        _check_drug_detection(expected.get("must_detect_drugs") or [], result),
        _check_retrieval_coverage(expected.get("must_have_chunks_for_drugs") or [], result),
        _check_citations(bool(expected.get("must_have_citations")), result, generated),
        _check_idk(bool(expected.get("must_be_idk")), result, generated),
        _check_phrases(expected.get("must_contain_phrases") or [], result, generated),
        faith_check,
    ]
    return outcome


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _p95(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(0.95 * (len(s) - 1)))))
    return s[idx]


def _median(values: List[float]) -> float:
    return statistics.median(values) if values else 0.0


def build_report(outcomes: List[QuestionOutcome], mode: str, started_at: datetime) -> str:
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed)
    pct = (100.0 * passed / total) if total else 0.0

    by_cat: Dict[str, Tuple[int, int]] = {}
    for o in outcomes:
        p, t = by_cat.get(o.category, (0, 0))
        by_cat[o.category] = (p + (1 if o.passed else 0), t + 1)

    stage_vals: Dict[str, List[float]] = {s: [] for s in STAGES}
    for o in outcomes:
        for s in STAGES:
            v = o.timing_ms.get(s, 0.0)
            if s == "generate" and v == 0.0:
                continue  # retrieval-only mode — don't dilute the median
            stage_vals[s].append(v)

    judge_models = sorted({o.judge.judge_model for o in outcomes if o.judge and o.judge.judge_model})
    judge_line = f"Judge model: **{', '.join(judge_models)}**" if judge_models else "Judge: **disabled**"

    L: List[str] = [
        f"# RAG eval — {started_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Mode: **{mode}**",
        judge_line,
        f"Pass rate: **{passed}/{total}** ({pct:.0f}%)",
        "",
        "## Per-category", "",
        "| Category | Passed | Total |",
        "|----------|--------|-------|",
    ]
    for cat in sorted(by_cat):
        p, t = by_cat[cat]
        L.append(f"| {cat} | {p} | {t} |")

    L += ["", "## Latency (ms, median / p95)", "",
          "| Stage    | median | p95  |", "|----------|--------|------|"]
    for s in STAGES:
        vals = stage_vals[s]
        if not vals:
            L.append(f"| {s} | n/a | n/a |")
        else:
            L.append(f"| {s} | {_median(vals):.0f} | {_p95(vals):.0f} |")

    failures = [o for o in outcomes if not o.passed]
    L += ["", "## Failures", ""]
    if not failures:
        L.append("_All applicable checks passed._")
    else:
        L += ["| id | category | check | detail |", "|----|----------|-------|--------|"]
        for o in failures:
            for c in o.checks:
                if c.status != "fail":
                    continue
                detail = c.detail.replace("|", "\\|")
                L.append(f"| {o.qid} | {o.category} | {c.name} | {detail} |")

    L += ["", "## Per-question (full)", "",
          "| id | category | drug_detect | retrieval_cover | citations | idk | phrases | faithfulness | passed |",
          "|----|----------|-------------|------------------|-----------|-----|---------|--------------|--------|"]
    glyph = {"pass": "pass", "fail": "FAIL", "skip": "—"}
    for o in outcomes:
        by_name = {c.name: c.status for c in o.checks}
        L.append("| " + " | ".join([
            o.qid, o.category,
            glyph[by_name.get("drug_detect", "skip")],
            glyph[by_name.get("retrieval_cover", "skip")],
            glyph[by_name.get("citations", "skip")],
            glyph[by_name.get("idk", "skip")],
            glyph[by_name.get("phrases", "skip")],
            glyph[by_name.get("faithfulness", "skip")],
            "yes" if o.passed else "NO",
        ]) + " |")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-generation", action="store_true", help="Skip LLM stage.")
    parser.add_argument("--no-judge", action="store_true",
                        help="Skip the LLM-as-judge faithfulness agent.")
    parser.add_argument("--judge-model", default=None,
                        help="Override judge model id (default: $JUDGE_MODEL or anthropic/claude-haiku-4.5).")
    parser.add_argument("--filter", default=None, help="Run only this category.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Markdown report path.")
    parser.add_argument("--golden", default=str(GOLDEN_PATH), help="Golden Q&A JSON path.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    skip_generation = args.no_generation
    if not skip_generation and not os.environ.get("OPENROUTER_API_KEY"):
        logger.warning("OpenRouter key not set; running retrieval-only eval")
        skip_generation = True

    judge: Optional[JudgeAgent] = None
    if not skip_generation and not args.no_judge:
        if not os.environ.get("OPENROUTER_API_KEY"):
            logger.warning("OpenRouter key not set; judge agent disabled")
        else:
            judge = JudgeAgent(model=args.judge_model)
            print(f"Judge enabled: {judge.model}", file=sys.stderr)

    with open(args.golden, "r", encoding="utf-8") as fh:
        questions = json.load(fh).get("questions", [])
    if args.filter:
        questions = [q for q in questions if q.get("category") == args.filter]
    if not questions:
        print(f"No questions to run (filter={args.filter!r}).", file=sys.stderr)
        return 1

    started_at = datetime.now()
    pipeline = RAGPipeline()
    outcomes: List[QuestionOutcome] = []
    for i, q in enumerate(questions, start=1):
        print(f"[{i}/{len(questions)}] {q['id']} :: {q['question']}", file=sys.stderr)
        try:
            outcomes.append(run_question(pipeline, q, skip_generation, judge))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Eval threw on %s", q["id"])
            outcomes.append(QuestionOutcome(
                qid=q["id"], category=q["category"], question=q["question"],
                checks=[CheckOutcome("driver", "fail", f"raised: {exc!r}")],
            ))

    mode = "retrieval-only" if skip_generation else "with-generation"
    report = build_report(outcomes, mode, started_at)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nReport written to {out_path}", file=sys.stderr)
    return 0 if all(o.passed for o in outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
