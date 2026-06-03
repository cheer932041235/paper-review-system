<p align="right"><b>English</b> | <a href="README.md">简体中文</a></p>

# Paper Review System

> A two-tier pre-submission review system for academic papers: a free, high-frequency **lightweight self-check**, plus a paid **multi-model review committee** — six different-vendor LLMs acting as six genuinely independent reviewers.

Use the lightweight self-check routinely to catch formatting / number / layout errors; escalate to the multi-model committee at the critical moment before submission for deep academic review, cross-validation, and triaged scoring.

---

## Why two tiers

Not every check deserves the most expensive tool. **A cheap, high-frequency self-check for daily use, plus an expensive multi-model review at the critical moment** — this cost discipline is the core design.

| | Lightweight self-check | Multi-model review |
|---|---|---|
| Run by | a human / agent reads directly | 6 external LLMs in parallel |
| Cost | free | ~50–70K tokens / round |
| Frequency | after every edit | final pass before submission |
| Catches | format + structure + **number consistency** + visual layout | deep academic review + cross-validation + scored triage |
| Fits | theses / EI papers usually enough | CCF / SCI before submission |

Full judgment rationale in [`docs/methodology.md`](docs/methodology.md).

---

## Tier 1 · Lightweight self-check (free)

Two dimensions in parallel — two pairs of eyes catch different errors:

- **Dimension A · Text content** (read the source): structural completeness, abstract↔body consistency, **number cross-validation (top priority)**, figure/table audit, symbol/formula consistency, references, language, logic chain, figure quality.
- **Dimension B · Visual layout** (PDF → per-page PNG, read visually): headers, TOC page numbers, blank pages, cropped figures, cross-page tables, formula rendering, whitespace… **correct source ≠ correct output**; layout errors only surface when you *look at* the rendered product.

```bash
python doc_to_pages.py path/to/paper.pdf --dpi 200
# .docx supported too; outputs per-page PNGs to review against the visual checklist
```

Guiding principle: **diagnose globally, treat precisely** — first screenshot all pages and build a complete issue list, then fix one thing at a time and recompile-and-verify immediately after each. Never batch-edit tightly-coupled systems (e.g. LaTeX floats).

---

## Tier 2 · Multi-model committee review (paid)

**Core belief: cross-model consensus ≫ repeating one model.** Different vendors = different training data = genuinely independent perspectives. When models from completely different sources flag the same issue, it is probably real; an issue raised by only one model may be its hallucination.

> 💡 **How do you get so many different-vendor models at once?** The committee specifically needs *multiple different vendors*, so an **aggregator API** is the easiest path — one key calls Claude / GPT / Gemini / Grok / DeepSeek and more, which fits this workflow perfectly. This project defaults to `vectorengine`, one such aggregator with broad, up-to-date model coverage: sign up → <https://api.vectorengine.ai/register?aff=jyFY>

Review committee (default config, editable in `config.json`):

| Role | Default model family | Responsibility |
|------|------|------|
| **EIC** | Anthropic | overall quality, originality, venue fit |
| **Methodology** | OpenAI | experimental design, statistics, reproducibility, data leakage |
| **Domain** | Google | literature, theory, incremental contribution |
| **Writing** | xAI | clarity, structure, figures |
| **Devil's Advocate** | DeepSeek | rebuttals, logical fallacies, overclaiming (deliberately harsh) |
| **Meta** | OpenAI reasoning | synthesize the 5 reviews, arbitrate, triage |

**Aggregation logic**: the 5 reviewers each score 6 dimensions (Originality / Soundness / Significance / Clarity / Reproducibility / Overall, 1–10) → each dimension is the **mean** of the 5 (with Min/Max as the disagreement range); Meta **does not score**, it takes the **union of all issues, deduplicates, and triages** them into `MUST FIX / NICE TO HAVE / ALREADY ADDRESSED / REVIEWER ERROR`.

**Triple-API graceful fallback**: each reviewer has a `fallback_chain`; when the primary model is rate-limited or unavailable it drops to a backup API or a slightly weaker model — never straight to a weak model.

**Venue calibration**: `--venue top|mid|regional` calibrates scoring and triage to the target venue tier.

### Quickstart

```bash
# 1. Configure API keys (your keys stay local; config.json is git-ignored)
cp config.example.json config.json
#    edit config.json with your real keys

# 2. Run the review
python paper_reviewer.py path/to/main.tex --config config.json --venue regional

# Optional: multi-round / subset of reviewers
python paper_reviewer.py main.tex --config config.json --venue mid --rounds 3
python paper_reviewer.py main.tex --config config.json --reviewers EIC Writing Devil
```

Produces `*_review.md` (human-readable: score summary + consensus/disagreement + revision roadmap) and `*_review.json` (machine-readable).

---

## How to read the scores (don't just look at the total)

1. **Absolute calibration**: 8 strong / 7 solid accept / 6 borderline / ≤5 below bar.
2. **Weight by venue**: top venues weight Originality + Significance; Q4 / engineering journals weight Soundness + Clarity + Reproducibility. The same scores can mean accept or reject depending on venue.
3. **Read the shape, not the total**: high execution + modest originality = a clean application paper; the Min–Max range shows how much reviewers disagree.

> Rule of thumb: **the total sets the pass/fail line, the shape defines the paper's character, the venue decides whether that shape is good.**

---

## End-to-end loop

```
draft done → lightweight self-check, 2–3 rounds → (EI papers stop here)
          → (CCF/SCI) multi-model review → read shape + triage → fix must-fix → re-verify → submit
```

## Security

- **Real API keys live only in your local `config.json`, which is `.gitignore`d and never enters the repo.**
- The repo ships only a scrubbed `config.example.json` (placeholders).
- The scripts embed no keys; they read only from `config.json`.

## Requirements

- Python 3.9+; `requests` (API calls).
- Visual review: `doc_to_pages.py` needs `pdftoppm` (poppler) or `.docx` conversion support.

## License

MIT © 2026 疏锦行

---

## Mentoring · Collaboration

For **research mentoring or collaboration**, contact **疏锦行 (Shu Jinxing)** on WeChat: **shujinxing777**
