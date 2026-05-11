# Product

## Register

product

## Users

Two overlapping audiences sit in front of this surface.

The primary live audience is a technical interviewer or evaluator clicking through the demo for the first time — they are skeptical of AI-glued-together RAG demos and are reading the screen for signs of rigor (citations, sources, redaction, dev panel). They land on the page, ask one or two questions, and form an opinion within thirty seconds about whether the system is grounded or hand-waving.

The secondary intended audience is a careful patient or clinician looking up an interaction between two named drugs, late at night, on a phone or laptop, expecting an answer with citations they can verify. They are not looking for chat company. They want a referenced answer fast.

Both audiences read carefully. Neither rewards theatrics.

## Product Purpose

Medication Reference answers natural-language questions about drug interactions, indications, pharmacokinetics, and FDA recalls — grounded in publicly available FDA DailyMed labels, NIH MedlinePlus monographs, and OpenFDA recall records. The interface exists to make the grounding visible: PII is stripped before retrieval, RxNorm normalises drug names, hybrid retrieval + cross-encoder rerank picks evidence, and the LLM is forced to cite. The UI's job is to surface that pipeline confidence without performing it.

Success is the evaluator saying "this is grounded" within one exchange, and the patient saying "I trust the citation" within one answer.

## Brand Personality

Three words: **measured, evidentiary, unshowy.**

Voice: a reference desk, not a chatbot. Quiet, specific, willing to say "I don't know." Citations are first-class, not decorative. Personal pronouns ("we," "your") are rare. The interface speaks like a printed monograph — declarative sentences, no flourish.

Emotional goal: relief. The user should feel they have landed at something that takes the question seriously, and leave with the page's answer plus a working link to the underlying FDA / NIH source.

## Anti-references

**Do not look like:**

- A generic "healthcare AI" SaaS landing — white + teal + sans-serif headings + gradient orb. This is the first-order reflex for the category and it screams AI.
- A ChatGPT skin — message bubbles, avatar circles, "Assistant" labels, sample-question card grids, eyebrow-heading-subtitle hero blocks.
- Observability-dashboard dark mode — navy plus light blue plus dual box-shadow cards.
- WebMD / consumer pharmacy — friendly green accents, stock photos, soft round everything.
- The "cozy editorial / Notion-cream" reflex on the opposite end — beige paper, big serif logo, narrative scrolling page. This is a tool, not a Substack.

Specifically avoid: pill-shaped buttons, 999px radii on everything, two-shadow cards, side-stripe coloured borders, gradient text, animated background gradients.

## Design Principles

1. **Show grounding through restraint, not chrome.** The strongest signal of rigor is that citations, sources, and the pipeline look like reference material — not like UI ornament. Strip until what remains is the evidence.
2. **Typography carries the brand.** Hierarchy comes from type scale and rule lines, not from cards, shadows, or coloured backgrounds. A single accent colour is used sparingly and only on things that point to evidence (citations, links, the ℞ mark).
3. **Documents over conversations.** A transcript that reads like a printed Q&A column is more credible than a chat shell. Avoid the message-bubble + avatar pattern; let questions and answers be typeset siblings.
4. **One committed accent.** Pick one colour and use it deliberately. Not a palette. The colour signifies "this points to evidence."
5. **The dev panel is a back room, not a feature.** Pipeline status, retrieval debug, redaction audit are gated behind dev mode for a reason — they're for evaluators verifying the demo, not patients. Their UI should look utilitarian, not promoted.

## Accessibility & Inclusion

- WCAG 2.2 AA contrast on body text and interactive controls in both themes.
- Respect `prefers-reduced-motion` — already honoured for the rail drawer; extend to any new motion.
- Keyboard navigable: tab order matches reading order, focus rings visible, Enter to send, Shift+Enter for newline (already implemented).
- Theme toggle (light / dark) persists per user and falls back to `prefers-color-scheme`.
- Citation links open in new tab with `rel="noopener noreferrer"` and visible underline so they are reachable without colour.
- Recall severity is conveyed by label text ("Class I recall") in addition to colour — never colour alone.
