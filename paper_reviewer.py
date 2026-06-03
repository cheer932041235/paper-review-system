#!/usr/bin/env python3
"""
Multi-Agent Paper Reviewer — 多模型独立审稿系统
Uses 6 different LLM families as independent reviewers for academic papers.
Cross-model consensus is far more reliable than same-model repetition.

Usage:
    python paper_reviewer.py <paper.tex> [--rounds N] [--output report.md] [--config config.json]
"""

import json
import sys
import os
import re
import time
import argparse
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import random
import copy

# ============================================================
# Default Configuration
# ============================================================

DEFAULT_API_SOURCES = {
    "vectorengine": {
        "url": "https://api.vectorengine.ai/v1/chat/completions",
        "key": os.environ.get("VECTORENGINE_API_KEY", ""),
        "token_param": "max_tokens"
    },
    "sophnet": {
        "url": "https://www.sophnet.com/api/open-apis/v1/chat/completions",
        "key": os.environ.get("SOPHNET_API_KEY", ""),
        "token_param": "max_completion_tokens"
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "token_param": "max_tokens"
    }
}

# 6 different model families → genuinely independent perspectives
# Each reviewer has a fallback chain: primary@vectorengine → same@sophnet → downgrade@vectorengine → downgrade@sophnet
DEFAULT_REVIEWERS = {
    "EIC": {
        "role": "Editor-in-Chief",
        "focus": "Overall quality, originality, significance, venue fit",
        "fallback_chain": [
            {"model": "claude-opus-4-6-thinking", "api": "vectorengine"},
            {"model": "claude-opus-4-6", "api": "vectorengine"},
            {"model": "claude-opus-4-6", "api": "sophnet"},
            {"model": "claude-sonnet-4-6", "api": "vectorengine"},
            {"model": "claude-sonnet-4-6", "api": "sophnet"},
        ]
    },
    "Methodology": {
        "role": "Methodology Reviewer",
        "focus": "Experimental design, statistical validity, reproducibility, baselines",
        "fallback_chain": [
            {"model": "o4-mini-all", "api": "vectorengine"},
            {"model": "gpt-5.5", "api": "vectorengine"},
            {"model": "gpt-5.5", "api": "sophnet"},
            {"model": "gpt-4o", "api": "vectorengine"},
        ]
    },
    "Domain": {
        "role": "Domain Expert",
        "focus": "Literature coverage, theoretical grounding, incremental contribution",
        "fallback_chain": [
            {"model": "gemini-3.1-pro-preview-thinking", "api": "vectorengine"},
            {"model": "gemini-3.1-pro-preview", "api": "vectorengine"},
            {"model": "gemini-3.1-pro-preview", "api": "sophnet"},
            {"model": "gpt-5.5", "api": "sophnet"},
        ]
    },
    "Writing": {
        "role": "Writing Quality Reviewer",
        "focus": "Clarity, structure, grammar, notation, figures, readability",
        "fallback_chain": [
            {"model": "grok-4.1", "api": "vectorengine"},
            {"model": "claude-sonnet-4-6", "api": "vectorengine"},
            {"model": "claude-sonnet-4-6", "api": "sophnet"},
            {"model": "gpt-5.5", "api": "sophnet"},
        ]
    },
    "Devil": {
        "role": "Devil's Advocate",
        "focus": "Counter-arguments, logical fallacies, cherry-picking, overclaiming",
        "fallback_chain": [
            {"model": "deepseek-v4-pro", "api": "deepseek"},
            {"model": "deepseek-v4-pro", "api": "vectorengine"},
            {"model": "DeepSeek-V4-Pro", "api": "sophnet"},
            {"model": "gpt-5.5", "api": "vectorengine"},
            {"model": "claude-sonnet-4-6", "api": "sophnet"},
        ]
    }
}

DEFAULT_META_REVIEWER = {
    "fallback_chain": [
        {"model": "o4-mini-all", "api": "vectorengine"},
        {"model": "gpt-5.5", "api": "vectorengine"},
        {"model": "gpt-5.5", "api": "sophnet"},
        {"model": "claude-sonnet-4-6", "api": "vectorengine"},
    ]
}

MIN_REVIEWERS = 3

SCORING_DIMENSIONS = [
    "originality", "soundness", "significance",
    "clarity", "reproducibility", "overall"
]

# Venue presets for calibrated reviewing
VENUE_PRESETS = {
    "top": {
        "label": "Top-tier (NeurIPS/ICML/ACL/CVPR)",
        "acceptance_rate": "~20%",
        "calibration": "Apply the highest standards. Expect novel contributions, rigorous evaluation with human studies, comprehensive baselines, and full reproducibility artifacts."
    },
    "mid": {
        "label": "Mid-tier (AAAI/IJCAI/EMNLP/CIKM, CCF-A/B)",
        "acceptance_rate": "~25-30%",
        "calibration": "Solid work with clear contributions is sufficient. Moderate novelty with thorough experiments is acceptable. Missing one type of experiment (e.g., human eval) is a weakness but not fatal."
    },
    "regional": {
        "label": "Regional/Workshop (IEEE SMC/WCISP/CSIC, CCF-C or unranked)",
        "acceptance_rate": "~35-50%",
        "calibration": "Focus on whether the work is technically sound and clearly presented. Incremental improvements with proper evaluation are acceptable. Do NOT demand top-venue standards like large-scale human evaluation, >5 baselines, or novel theoretical contributions. A practical engineering contribution with proper ablations is sufficient."
    }
}

VENUE_INJECTION = """

=== VENUE CALIBRATION ===
Target venue: {venue_label} (acceptance rate: {acceptance_rate})
Calibration: {calibration}

IMPORTANT reviewing guidelines for this venue level:
1. Calibrate your scores to this venue's bar, NOT to NeurIPS/ICML standards.
2. Give CREDIT to authors who proactively disclose limitations — do NOT re-penalize issues the paper already acknowledges.
3. Consider page limits: a 6-page conference paper cannot include everything.
4. Distinguish between 'must-fix for this venue' vs 'would improve but not required'.
5. A score of 5-6 means borderline for THIS venue, not for a top venue.
=== END VENUE CALIBRATION ===
"""

# ============================================================
# Reviewer Prompts
# ============================================================

REVIEWER_SYSTEM_PROMPTS = {
    "EIC": """You are the Editor-in-Chief of a top-tier AI/systems conference (NeurIPS, ICML, IEEE SMC, EMNLP).
You evaluate papers for overall quality, originality, significance, and venue fit.
You have 20+ years of experience reviewing AI research papers.

Focus areas:
- Is the contribution clear and novel?
- Is the paper significant to the community?
- Does it meet the quality bar of a top venue?
- Are the claims well-supported by evidence?

You must output ONLY a valid JSON object (no markdown, no explanation outside JSON).""",

    "Methodology": """You are a senior methodology reviewer specializing in experimental design and statistical analysis.
You have deep expertise in evaluation metrics, baseline comparisons, and reproducibility standards.

Focus areas:
- Is the experimental design rigorous and fair?
- Are baselines appropriate and up-to-date?
- Are ablation studies sufficient?
- Are results statistically significant (error bars, p-values, multiple seeds)?
- Could another researcher reproduce these results?
- Are there any methodological flaws or confounds?

You must output ONLY a valid JSON object (no markdown, no explanation outside JSON).""",

    "Domain": """You are a domain expert reviewer with broad knowledge of the research landscape.
You evaluate how the paper positions itself within the field and its incremental contribution.

Focus areas:
- Is the related work comprehensive and fair?
- Are key references missing?
- Is the theoretical framework sound?
- How does this advance the state of the art?
- Is the problem formulation well-motivated?
- Are there connections to other subfields that the authors missed?

You must output ONLY a valid JSON object (no markdown, no explanation outside JSON).""",

    "Writing": """You are a writing quality reviewer who evaluates clarity, organization, and presentation.
You focus on whether the paper communicates its ideas effectively to the target audience.

Focus areas:
- Is the abstract informative and self-contained?
- Does the introduction clearly state the problem and contributions?
- Is the paper well-organized with logical flow?
- Are there grammatical errors or awkward phrasing?
- Is notation consistent throughout?
- Are figures and tables clear, well-captioned, and referenced in text?
- Is the paper the right length (no padding, no critical omissions)?

You must output ONLY a valid JSON object (no markdown, no explanation outside JSON).""",

    "Devil": """You are the Devil's Advocate reviewer. Your job is to be constructively adversarial.
Find the strongest possible counter-arguments against the paper's core claims.

Focus areas:
- What is the strongest argument AGAINST this paper's main claim?
- Is there cherry-picking in results or evaluation?
- Are there alternative explanations the authors didn't consider?
- Is there confirmation bias in the experimental design?
- Are the claims overgeneralized beyond what the evidence supports?
- What would make a reviewer reject this paper?
- What's the "so what?" — does this actually matter?

Be rigorous but fair. Identify real issues, not nitpicks.
You must output ONLY a valid JSON object (no markdown, no explanation outside JSON)."""
}

REVIEW_OUTPUT_FORMAT = """
Output the following JSON structure exactly:
{
  "scores": {
    "originality": <1-10>,
    "soundness": <1-10>,
    "significance": <1-10>,
    "clarity": <1-10>,
    "reproducibility": <1-10>,
    "overall": <1-10>,
    "confidence": <1-5>
  },
  "decision": "<strong_accept|accept|weak_accept|borderline|weak_reject|reject|strong_reject>",
  "summary": "<2-3 sentence summary of the paper>",
  "strengths": ["S1: ...", "S2: ...", "S3: ..."],
  "weaknesses": ["W1: ...", "W2: ...", "W3: ..."],
  "questions": ["Q1: ...", "Q2: ..."],
  "suggestions": ["Fix1: ...", "Fix2: ..."],
  "critical_issues": ["<empty list if none, or list issues that MUST be fixed>"]
}"""

META_REVIEW_PROMPT = """You are the Meta-Reviewer (Area Chair). You have received {n} independent reviews of the same paper from different experts using different AI models. Your job is to:

1. Identify consensus points (issues raised by 2+ reviewers)
2. Identify disagreements and arbitrate them
3. Weight reviews by confidence scores
4. Produce a final decision and a TRIAGED revision roadmap
{venue_context}
CRITICAL triage rules:
- If a reviewer raises an issue that the paper ALREADY explicitly acknowledges/discusses, classify it as "acknowledged" (do NOT treat it as a new weakness — give credit to the authors for transparency).
- If a reviewer demands something infeasible within page limits (e.g., 300-sample human eval in a 6-page paper), classify it as "nice_to_have".
- If a reviewer's claim contains a factual error (e.g., mischaracterizing a standard metric), note the error in your arbitration.
- Only classify as "must_fix" issues that are genuine gaps NOT already addressed by the paper AND feasible within the paper's constraints.

Reviews:
{reviews_text}

Output the following JSON structure exactly:
{{
  "score_summary": {{
    "originality": {{"mean": <float>, "min": <int>, "max": <int>}},
    "soundness": {{"mean": <float>, "min": <int>, "max": <int>}},
    "significance": {{"mean": <float>, "min": <int>, "max": <int>}},
    "clarity": {{"mean": <float>, "min": <int>, "max": <int>}},
    "reproducibility": {{"mean": <float>, "min": <int>, "max": <int>}},
    "overall": {{"mean": <float>, "min": <int>, "max": <int>}}
  }},
  "consensus_strengths": ["<strengths mentioned by 2+ reviewers>"],
  "consensus_weaknesses": ["<weaknesses mentioned by 2+ reviewers>"],
  "disputed_points": [{{"point": "...", "for": ["reviewer names"], "against": ["reviewer names"], "arbitration": "your judgment"}}],
  "final_decision": "<accept|weak_accept|borderline|weak_reject|reject>",
  "decision_rationale": "<3-5 sentences explaining the decision>",
  "revision_roadmap": [
    {{"priority": 1, "issue": "...", "suggested_fix": "...", "raised_by": ["reviewer names"], "triage": "<must_fix|nice_to_have|acknowledged|reviewer_error>", "triage_reason": "..."}}
  ]
}}"""

# ============================================================
# API Client
# ============================================================

def call_llm(model, system_prompt, user_prompt, api_source, temperature=0.3, max_tokens=4096):
    """Call an LLM via OpenAI-compatible API. Returns (response_text, usage_dict).
    api_source: dict with keys 'url', 'key', 'token_param', optionally 'name'."""
    # Thinking/reasoning models need higher token budget and longer timeout
    thinking_patterns = ["thinking", "o3", "o4", "r1", "gpt-5.5", "gpt-5-", "deepseek-v4"]
    is_thinking = any(p in model.lower() for p in thinking_patterns)
    if is_thinking:
        max_tokens = max(max_tokens, 16384)
        temperature = 1  # Reasoning models require temperature=1
    timeout = 600 if is_thinking else 300

    api_url = api_source["url"]
    api_key = api_source["key"]
    token_param = api_source.get("token_param", "max_tokens")
    api_name = api_source.get("name", "?")

    # Detect if this is a Claude model on sophnet (Anthropic Messages API format)
    is_claude_on_sophnet = ("claude" in model.lower() and "sophnet" in api_name.lower())

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Sophnet routes Claude through Anthropic's Messages API which doesn't support
    # "system" as a message role — merge it into the user message instead
    if is_claude_on_sophnet:
        merged_user = f"{system_prompt}\n\n---\n\n{user_prompt}"
        messages = [{"role": "user", "content": merged_user}]
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        token_param: max_tokens
    }
    max_retries = 2  # Reduced: fallback chain handles broader recovery
    for attempt in range(max_retries):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return text, usage
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                print(f"    Timeout ({timeout}s), retrying in {wait:.0f}s...", flush=True)
                time.sleep(wait)
                continue
            return None, {"error": f"Timeout calling {model}@{api_name} after {max_retries} attempts"}
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            # Only retry on 5xx server errors; 4xx (model not found, quota) → fail fast to fallback
            if status_code >= 500 and attempt < max_retries - 1:
                wait = 15 * (2 ** attempt) + random.uniform(0, 5)
                print(f"    HTTP {status_code}, retrying in {wait:.0f}s...", flush=True)
                time.sleep(wait)
                continue
            error_msg = str(e)
            try:
                error_msg = e.response.json().get("message", error_msg)[:120]
            except Exception:
                pass
            return None, {"error": f"HTTP {status_code}: {error_msg}", "status_code": status_code}
        except Exception as e:
            return None, {"error": f"Error: {str(e)[:120]}"}


def call_llm_with_fallback(fallback_chain, api_sources, system_prompt, user_prompt,
                           temperature=0.3, max_tokens=4096, label=""):
    """Try each model+api in the fallback chain until one succeeds.
    Returns (response_text, usage_dict, model_used, api_used)."""
    attempts_log = []
    for i, step in enumerate(fallback_chain):
        model = step["model"]
        api_name = step["api"]
        api_source = api_sources.get(api_name)
        if not api_source or not api_source.get("key"):
            attempts_log.append(f"{model}@{api_name}: skipped (no API key)")
            continue

        api_with_name = {**api_source, "name": api_name}
        is_primary = (i == 0)
        if not is_primary:
            print(f"    {label}Trying fallback {i}/{len(fallback_chain)-1}: {model}@{api_name}...", flush=True)

        text, usage = call_llm(model, system_prompt, user_prompt, api_with_name,
                               temperature=temperature, max_tokens=max_tokens)
        if text is not None:
            if not is_primary:
                print(f"    {label}Fallback succeeded: {model}@{api_name}", flush=True)
            return text, usage, model, api_name

        error = usage.get("error", "unknown")
        attempts_log.append(f"{model}@{api_name}: {error}")
        print(f"    {label}Failed: {model}@{api_name} — {error}", flush=True)

    # All attempts failed
    return None, {"error": f"All {len(fallback_chain)} fallback attempts failed", "attempts": attempts_log}, None, None


def extract_json(text):
    """Extract JSON from LLM response, handling markdown code blocks, thinking tags, and extra text."""
    if text is None:
        return None
    # Strip DeepSeek-style <think>...</think> blocks
    text_clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    # Try direct parse on cleaned text
    try:
        return json.loads(text_clean)
    except json.JSONDecodeError:
        pass
    # Try direct parse on original text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    for t in [text_clean, text]:
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    # Try finding first { ... } block (use cleaned text first)
    for t in [text_clean, text]:
        depth = 0
        start = None
        for i, c in enumerate(t):
            if c == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(t[start:i+1])
                    except json.JSONDecodeError:
                        start = None
    # Last resort: try to repair truncated JSON (e.g., max_tokens hit)
    for t in [text_clean, text]:
        repaired = _repair_truncated_json(t)
        if repaired is not None:
            return repaired
    return None


def _repair_truncated_json(text):
    """Attempt to repair JSON that was truncated mid-output (e.g., max_tokens hit).
    Strategy: find the opening '{', then close any open strings, arrays, objects."""
    start = text.find('{')
    if start < 0:
        return None
    fragment = text[start:]
    # Close any open quoted string
    in_string = False
    escape = False
    for c in fragment:
        if escape:
            escape = False
            continue
        if c == '\\':
            escape = True
            continue
        if c == '"':
            in_string = not in_string
    if in_string:
        fragment += '"'
    # Count open brackets/braces and close them
    depth_brace = 0
    depth_bracket = 0
    in_str = False
    esc = False
    for c in fragment:
        if esc:
            esc = False
            continue
        if c == '\\':
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '{':
            depth_brace += 1
        elif c == '}':
            depth_brace -= 1
        elif c == '[':
            depth_bracket += 1
        elif c == ']':
            depth_bracket -= 1
    # Strip trailing comma before closing
    fragment = fragment.rstrip()
    if fragment.endswith(','):
        fragment = fragment[:-1]
    # Close open brackets then braces
    fragment += ']' * max(0, depth_bracket)
    fragment += '}' * max(0, depth_brace)
    try:
        return json.loads(fragment)
    except json.JSONDecodeError:
        return None

# ============================================================
# Paper Parser
# ============================================================

def read_paper(path):
    """Read a LaTeX or text file and return its content."""
    p = Path(path)
    if not p.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)
    text = p.read_text(encoding="utf-8", errors="replace")
    # Strip common LaTeX boilerplate but keep content
    # Remove comments
    text = re.sub(r'(?<!\\)%.*$', '', text, flags=re.MULTILINE)
    return text

# ============================================================
# Review Pipeline
# ============================================================

def run_single_review(reviewer_name, reviewer_cfg, paper_text, api_sources, venue_info=None):
    """Run a single reviewer with fallback chain. Returns structured review."""
    fallback_chain = reviewer_cfg.get("fallback_chain", [])
    # Backward compatibility: old format with single "model" field
    if not fallback_chain and "model" in reviewer_cfg:
        fallback_chain = [{"model": reviewer_cfg["model"], "api": "vectorengine"}]
    if not fallback_chain:
        print(f"  [{reviewer_name}] No models configured, skipping.", flush=True)
        return None

    system_prompt = REVIEWER_SYSTEM_PROMPTS[reviewer_name]
    if venue_info:
        system_prompt += VENUE_INJECTION.format(**venue_info)

    venue_instruction = "Evaluate it as if you were reviewing for a top-tier conference."
    if venue_info:
        venue_instruction = f"Evaluate it for: {venue_info['venue_label']} (acceptance ~{venue_info['acceptance_rate']}). Calibrate your standards accordingly."

    user_prompt = f"""Please review the following academic paper. {venue_instruction}

{REVIEW_OUTPUT_FORMAT}

--- PAPER START ---
{paper_text}
--- PAPER END ---"""

    primary = fallback_chain[0]
    print(f"  [{reviewer_name}] Sending to {primary['model']}@{primary['api']}...", flush=True)
    t0 = time.time()
    raw_text, usage, model_used, api_used = call_llm_with_fallback(
        fallback_chain, api_sources, system_prompt, user_prompt,
        label=f"[{reviewer_name}] "
    )
    elapsed = time.time() - t0

    if raw_text is None:
        print(f"  [{reviewer_name}] FAILED after all fallbacks: {usage.get('error', 'unknown')}")
        return None

    review = extract_json(raw_text)
    if review is None:
        print(f"  [{reviewer_name}] Warning: Could not parse JSON, saving raw text")
        review = {"raw_text": raw_text, "parse_error": True}

    fallback_used = model_used != primary["model"] or api_used != primary["api"]
    review["_meta"] = {
        "reviewer": reviewer_name,
        "role": reviewer_cfg["role"],
        "model": model_used,
        "api_source": api_used,
        "primary_model": primary["model"],
        "fallback_used": fallback_used,
        "elapsed_seconds": round(elapsed, 1),
        "usage": usage
    }

    scores = review.get("scores", {})
    overall = scores.get("overall", "?")
    decision = review.get("decision", "?")
    fb_tag = f" (fallback→{model_used}@{api_used})" if fallback_used else ""
    print(f"  [{reviewer_name}] Done in {elapsed:.1f}s — overall={overall}, decision={decision}{fb_tag}")
    return review


def run_all_reviews(paper_text, reviewers, api_sources, max_workers=5, venue_info=None, save_callback=None):
    """Run all reviewers in parallel with fallback chains. Returns dict of {name: review}."""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                run_single_review, name, cfg, paper_text, api_sources, venue_info
            ): name
            for name, cfg in reviewers.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                review = future.result()
                if review is not None:
                    results[name] = review
                    # Incremental save after each reviewer completes
                    if save_callback:
                        save_callback(results, phase="reviews")
                    print(f"\n  Progress: {len(results)}/{len(reviewers)} reviews completed.\n", flush=True)
            except Exception as e:
                print(f"  [{name}] Exception: {e}")
    return results


def run_meta_review(reviews, paper_text, api_sources, meta_cfg, venue_info=None):
    """Run meta-reviewer with fallback chain to synthesize all individual reviews."""
    fallback_chain = meta_cfg.get("fallback_chain", [])
    if not fallback_chain and "model" in meta_cfg:
        fallback_chain = [{"model": meta_cfg["model"], "api": "vectorengine"}]
    if not fallback_chain:
        fallback_chain = DEFAULT_META_REVIEWER["fallback_chain"]

    reviews_text = ""
    for name, review in reviews.items():
        meta = review.get("_meta", {})
        reviews_text += f"\n### {name} ({meta.get('role', '')}) — Model: {meta.get('model', '')}\n"
        # Remove _meta for cleaner display
        display = {k: v for k, v in review.items() if k != "_meta"}
        reviews_text += json.dumps(display, indent=2, ensure_ascii=False) + "\n"

    venue_context = ""
    if venue_info:
        venue_context = f"\nTarget venue: {venue_info['venue_label']} (acceptance rate: {venue_info['acceptance_rate']})\nVenue calibration: {venue_info['calibration']}\n"

    prompt = META_REVIEW_PROMPT.format(n=len(reviews), reviews_text=reviews_text, venue_context=venue_context)

    primary = fallback_chain[0]
    print(f"\n  [Meta-Reviewer] Synthesizing {len(reviews)} reviews with {primary['model']}@{primary['api']}...", flush=True)
    t0 = time.time()
    raw_text, usage, model_used, api_used = call_llm_with_fallback(
        fallback_chain, api_sources,
        "You are a Meta-Reviewer (Area Chair) at a top AI conference. Synthesize the reviews and make a final decision. Output ONLY valid JSON.",
        prompt,
        temperature=0.2, max_tokens=4096,
        label="[Meta-Reviewer] "
    )
    elapsed = time.time() - t0

    if raw_text is None:
        print(f"  [Meta-Reviewer] FAILED after all fallbacks: {usage.get('error', 'unknown')}")
        return None

    meta_review = extract_json(raw_text)
    if meta_review is None:
        print(f"  [Meta-Reviewer] Warning: Could not parse JSON")
        meta_review = {"raw_text": raw_text, "parse_error": True}

    fallback_used = model_used != primary["model"] or api_used != primary["api"]
    meta_review["_meta"] = {
        "model": model_used,
        "api_source": api_used,
        "primary_model": primary["model"],
        "fallback_used": fallback_used,
        "elapsed_seconds": round(elapsed, 1),
        "usage": usage
    }

    decision = meta_review.get("final_decision", "?")
    fb_tag = f" (fallback→{model_used}@{api_used})" if fallback_used else ""
    print(f"  [Meta-Reviewer] Done in {elapsed:.1f}s — final_decision={decision}{fb_tag}")
    return meta_review

# ============================================================
# Report Generator
# ============================================================

def generate_report(reviews, meta_review, paper_path, round_num=1):
    """Generate a formatted Markdown report."""
    lines = []
    lines.append(f"# Multi-Agent Paper Review Report")
    lines.append(f"")
    lines.append(f"- **Paper**: `{paper_path}`")
    lines.append(f"- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- **Round**: {round_num}")
    lines.append(f"- **Reviewers**: {len(reviews)} independent models")
    lines.append(f"")

    # Score summary table
    if meta_review and "score_summary" in meta_review:
        ss = meta_review["score_summary"]
        lines.append("## Score Summary")
        lines.append("")
        lines.append("| Dimension | Mean | Min | Max |")
        lines.append("|-----------|------|-----|-----|")
        for dim in SCORING_DIMENSIONS:
            if dim in ss:
                d = ss[dim]
                lines.append(f"| **{dim.capitalize()}** | {d.get('mean', '?')} | {d.get('min', '?')} | {d.get('max', '?')} |")
        lines.append("")

    # Final decision
    if meta_review:
        decision = meta_review.get("final_decision", "N/A")
        rationale = meta_review.get("decision_rationale", "")
        lines.append(f"## Final Decision: **{decision.upper()}**")
        lines.append(f"")
        lines.append(f"{rationale}")
        lines.append(f"")

    # Consensus
    if meta_review and "consensus_strengths" in meta_review:
        lines.append("## Consensus Strengths")
        for s in meta_review.get("consensus_strengths", []):
            lines.append(f"- {s}")
        lines.append("")

    if meta_review and "consensus_weaknesses" in meta_review:
        lines.append("## Consensus Weaknesses")
        for w in meta_review.get("consensus_weaknesses", []):
            lines.append(f"- {w}")
        lines.append("")

    # Disputed points
    if meta_review and meta_review.get("disputed_points"):
        lines.append("## Disputed Points")
        for dp in meta_review["disputed_points"]:
            lines.append(f"- **{dp.get('point', '?')}**")
            lines.append(f"  - For: {', '.join(dp.get('for', []))}")
            lines.append(f"  - Against: {', '.join(dp.get('against', []))}")
            lines.append(f"  - Arbitration: {dp.get('arbitration', '')}")
        lines.append("")

    # Revision roadmap
    if meta_review and meta_review.get("revision_roadmap"):
        lines.append("## Revision Roadmap (Triaged)")
        lines.append("")
        triage_icons = {
            "must_fix": "MUST FIX",
            "nice_to_have": "NICE TO HAVE",
            "acknowledged": "ALREADY ADDRESSED",
            "reviewer_error": "REVIEWER ERROR"
        }
        lines.append("| Priority | Triage | Issue | Suggested Fix | Raised By |")
        lines.append("|----------|--------|-------|---------------|-----------|")
        for item in meta_review["revision_roadmap"]:
            p = item.get("priority", "?")
            triage = item.get("triage", "must_fix")
            triage_label = triage_icons.get(triage, triage)
            issue = item.get("issue", "?")
            fix = item.get("suggested_fix", "?")
            by = ", ".join(item.get("raised_by", []))
            reason = item.get("triage_reason", "")
            lines.append(f"| P{p} | **{triage_label}** | {issue} | {fix} | {by} |")
        lines.append("")

        # Triage summary
        triage_counts = {}
        for item in meta_review["revision_roadmap"]:
            t = item.get("triage", "must_fix")
            triage_counts[t] = triage_counts.get(t, 0) + 1
        must_fix_count = triage_counts.get("must_fix", 0)
        total = len(meta_review["revision_roadmap"])
        lines.append(f"> **Triage Summary**: {must_fix_count}/{total} issues are MUST FIX. "
                     f"The rest are already addressed by the paper, nice-to-have improvements, or reviewer errors.")
        lines.append("")

    # Individual reviews
    lines.append("---")
    lines.append("")
    lines.append("## Individual Reviews")
    lines.append("")

    for name, review in reviews.items():
        meta = review.get("_meta", {})
        scores = review.get("scores", {})
        lines.append(f"### {name} — {meta.get('role', '')} ({meta.get('model', '')})")
        lines.append(f"")

        if scores:
            score_str = " | ".join(f"**{k[:4]}**={v}" for k, v in scores.items())
            lines.append(f"Scores: {score_str}")
            lines.append(f"")

        decision = review.get("decision", "?")
        lines.append(f"**Decision**: {decision}")
        lines.append(f"")

        if review.get("summary"):
            lines.append(f"**Summary**: {review['summary']}")
            lines.append(f"")

        if review.get("strengths"):
            lines.append("**Strengths**:")
            for s in review["strengths"]:
                lines.append(f"- {s}")
            lines.append("")

        if review.get("weaknesses"):
            lines.append("**Weaknesses**:")
            for w in review["weaknesses"]:
                lines.append(f"- {w}")
            lines.append("")

        if review.get("questions"):
            lines.append("**Questions**:")
            for q in review["questions"]:
                lines.append(f"- {q}")
            lines.append("")

        if review.get("suggestions"):
            lines.append("**Suggestions**:")
            for s in review["suggestions"]:
                lines.append(f"- {s}")
            lines.append("")

        if review.get("critical_issues"):
            lines.append("**Critical Issues** ⚠️:")
            for c in review["critical_issues"]:
                lines.append(f"- {c}")
            lines.append("")

        if review.get("parse_error"):
            lines.append(f"⚠️ JSON parse failed. Raw response saved in JSON output.")
            lines.append("")

        elapsed = meta.get("elapsed_seconds", "?")
        lines.append(f"*Model: {meta.get('model', '?')} | Time: {elapsed}s*")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Cost summary
    lines.append("## Cost Summary")
    lines.append("")
    total_input = 0
    total_output = 0
    for name, review in reviews.items():
        usage = review.get("_meta", {}).get("usage", {})
        total_input += usage.get("prompt_tokens", 0)
        total_output += usage.get("completion_tokens", 0)
    if meta_review:
        usage = meta_review.get("_meta", {}).get("usage", {})
        total_input += usage.get("prompt_tokens", 0)
        total_output += usage.get("completion_tokens", 0)
    lines.append(f"- Input tokens: ~{total_input:,}")
    lines.append(f"- Output tokens: ~{total_output:,}")
    lines.append(f"- Total tokens: ~{total_input + total_output:,}")
    lines.append("")

    return "\n".join(lines)

# ============================================================
# Main
# ============================================================

def load_config(config_path):
    """Load configuration from JSON file."""
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def normalize_api_sources(config, cli_api_key=None, cli_api_url=None):
    """Build api_sources from config, with backward compatibility and CLI overrides."""
    api_sources = config.get("api_sources", copy.deepcopy(DEFAULT_API_SOURCES))

    # Backward compat: old single api_url/api_key → vectorengine source
    if "api_sources" not in config:
        if config.get("api_url"):
            api_sources.setdefault("vectorengine", copy.deepcopy(DEFAULT_API_SOURCES.get("vectorengine", {})))
            api_sources["vectorengine"]["url"] = config["api_url"]
        if config.get("api_key"):
            api_sources.setdefault("vectorengine", copy.deepcopy(DEFAULT_API_SOURCES.get("vectorengine", {})))
            api_sources["vectorengine"]["key"] = config["api_key"]

    # CLI overrides apply to vectorengine
    if cli_api_key:
        api_sources.setdefault("vectorengine", copy.deepcopy(DEFAULT_API_SOURCES.get("vectorengine", {})))
        api_sources["vectorengine"]["key"] = cli_api_key
    if cli_api_url:
        api_sources.setdefault("vectorengine", copy.deepcopy(DEFAULT_API_SOURCES.get("vectorengine", {})))
        api_sources["vectorengine"]["url"] = cli_api_url

    return api_sources


def normalize_reviewers(config):
    """Normalize reviewer config with backward compatibility for old 'model' format."""
    reviewers = config.get("reviewers", DEFAULT_REVIEWERS)
    for name, cfg in reviewers.items():
        if "fallback_chain" not in cfg and "model" in cfg:
            cfg["fallback_chain"] = [{"model": cfg["model"], "api": "vectorengine"}]
    return reviewers


def normalize_meta_cfg(config):
    """Normalize meta-reviewer config with backward compatibility."""
    meta_cfg = config.get("meta_reviewer", {})
    if not meta_cfg:
        if "meta_reviewer_model" in config:
            meta_cfg = {"fallback_chain": [{"model": config["meta_reviewer_model"], "api": "vectorengine"}]}
        else:
            meta_cfg = dict(DEFAULT_META_REVIEWER)
    return meta_cfg


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Agent Paper Reviewer — 6 LLM families as independent reviewers"
    )
    parser.add_argument("paper", help="Path to LaTeX or text file")
    parser.add_argument("--rounds", type=int, default=1, help="Number of review rounds (default: 1)")
    parser.add_argument("--output", "-o", help="Output markdown report path (default: <paper>_review.md)")
    parser.add_argument("--json-output", help="Save raw JSON reviews (default: <paper>_review.json)")
    parser.add_argument("--config", help="Path to config JSON file")
    parser.add_argument("--api-key", help="API key for vectorengine (or set VECTORENGINE_API_KEY env var)")
    parser.add_argument("--api-url", help="API endpoint URL for vectorengine")
    parser.add_argument("--skip-meta", action="store_true", help="Skip meta-review synthesis")
    parser.add_argument("--reviewers", nargs="+", choices=list(DEFAULT_REVIEWERS.keys()),
                        help="Select specific reviewers (default: all)")
    parser.add_argument("--venue", choices=list(VENUE_PRESETS.keys()),
                        help="Target venue tier for calibrated reviewing: top/mid/regional")
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Build api_sources (dual API with backward compat)
    api_sources = normalize_api_sources(config, cli_api_key=args.api_key, cli_api_url=args.api_url)

    # Validate at least one API source has a key
    active_sources = {k: v for k, v in api_sources.items() if v.get("key")}
    if not active_sources:
        print("Error: No API keys configured. Set VECTORENGINE_API_KEY env var, use --api-key, or configure api_sources in config.json", file=sys.stderr)
        sys.exit(1)

    # Select reviewers and meta config
    reviewers = normalize_reviewers(config)
    if args.reviewers:
        reviewers = {k: v for k, v in reviewers.items() if k in args.reviewers}
    meta_cfg = normalize_meta_cfg(config)
    min_reviewers = config.get("min_reviewers", MIN_REVIEWERS)

    # Output paths
    paper_stem = Path(args.paper).stem
    paper_dir = Path(args.paper).parent
    output_md = args.output or str(paper_dir / f"{paper_stem}_review.md")
    output_json = args.json_output or str(paper_dir / f"{paper_stem}_review.json")

    # Build venue info
    venue_info = None
    if args.venue:
        preset = VENUE_PRESETS[args.venue]
        venue_info = {
            "venue_label": preset["label"],
            "acceptance_rate": preset["acceptance_rate"],
            "calibration": preset["calibration"]
        }
    elif config.get("venue"):
        preset = VENUE_PRESETS.get(config["venue"])
        if preset:
            venue_info = {
                "venue_label": preset["label"],
                "acceptance_rate": preset["acceptance_rate"],
                "calibration": preset["calibration"]
            }

    # Display configuration
    def get_primary_model(cfg):
        chain = cfg.get("fallback_chain", [])
        if chain:
            return f"{chain[0]['model']}@{chain[0]['api']}"
        return cfg.get("model", "?")

    print(f"\n{'='*60}")
    print(f"  Multi-Agent Paper Reviewer (Dual-API Fallback)")
    print(f"  Paper: {args.paper}")
    print(f"  Venue: {venue_info['venue_label'] if venue_info else 'Not specified (default: top-tier)'}")
    print(f"  Reviewers: {', '.join(reviewers.keys())}")
    print(f"  Primary models:")
    for rname, rcfg in reviewers.items():
        print(f"    {rname}: {get_primary_model(rcfg)}")
    meta_primary = get_primary_model(meta_cfg)
    print(f"  Meta-Reviewer: {meta_primary}")
    src_status = ", ".join(f"{k}({'✓' if v.get('key') else '✗'})" for k, v in api_sources.items())
    print(f"  API Sources: {src_status}")
    print(f"  Min reviewers: {min_reviewers}")
    print(f"  Rounds: {args.rounds}")
    print(f"{'='*60}\n")

    paper_text = read_paper(args.paper)
    print(f"Paper loaded: {len(paper_text):,} characters\n")

    all_rounds = []

    # Incremental save callback
    def save_incremental(results, phase="reviews"):
        try:
            interim = {"round": "in_progress", "phase": phase, "reviews": results, "timestamp": datetime.now().isoformat()}
            interim_path = output_json.replace(".json", "_interim.json")
            with open(interim_path, "w", encoding="utf-8") as f:
                json.dump(interim, f, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pass  # Don't let save errors interrupt the review

    for round_num in range(1, args.rounds + 1):
        print(f"{'='*40}")
        print(f"  ROUND {round_num}/{args.rounds}")
        print(f"{'='*40}\n")

        # Phase 1: Independent Reviews
        print("Phase 1: Independent Reviews")
        reviews = run_all_reviews(paper_text, reviewers, api_sources, venue_info=venue_info, save_callback=save_incremental)

        if not reviews:
            print("Error: No reviews completed successfully.", file=sys.stderr)
            sys.exit(1)

        print(f"\n  {len(reviews)}/{len(reviewers)} reviews completed.", flush=True)

        # Check minimum reviewers
        if len(reviews) < min_reviewers:
            print(f"  Warning: Only {len(reviews)}/{len(reviewers)} reviews succeeded (minimum: {min_reviewers}).", file=sys.stderr)
            print(f"  Proceeding with available reviews, but results may be less reliable.\n", file=sys.stderr)

        # Log fallback usage
        fallback_count = sum(1 for r in reviews.values() if r.get("_meta", {}).get("fallback_used"))
        if fallback_count:
            print(f"  Note: {fallback_count} reviewer(s) used fallback models.\n", flush=True)

        # Phase 2: Meta-Review
        meta_review = None
        if not args.skip_meta and len(reviews) >= 2:
            print("Phase 2: Meta-Review Synthesis")
            meta_review = run_meta_review(reviews, paper_text, api_sources, meta_cfg, venue_info=venue_info)

        # Generate report
        report = generate_report(reviews, meta_review, args.paper, round_num)

        round_data = {
            "round": round_num,
            "reviews": reviews,
            "meta_review": meta_review,
            "timestamp": datetime.now().isoformat()
        }
        all_rounds.append(round_data)

        # Save outputs
        with open(output_md, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  Report saved: {output_md}")

        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(all_rounds, f, indent=2, ensure_ascii=False, default=str)
        print(f"  JSON saved: {output_json}")

        # Clean up interim file
        interim_path = output_json.replace(".json", "_interim.json")
        if Path(interim_path).exists():
            Path(interim_path).unlink()

        # Print quick summary
        if meta_review and "final_decision" in meta_review:
            decision = meta_review["final_decision"]
            print(f"\n  {'='*40}")
            print(f"  ROUND {round_num} DECISION: {decision.upper()}")
            if meta_review.get("score_summary", {}).get("overall"):
                overall = meta_review["score_summary"]["overall"]
                print(f"  Overall Score: {overall.get('mean', '?')} (range {overall.get('min', '?')}-{overall.get('max', '?')})")
            roadmap = meta_review.get("revision_roadmap", [])
            if roadmap:
                print(f"  Top Issues:")
                for item in roadmap[:3]:
                    print(f"    P{item.get('priority', '?')}: {item.get('issue', '?')}")
            print(f"  {'='*40}\n")

    print(f"\nDone. {len(all_rounds)} round(s) completed.")
    print(f"  Report: {output_md}")
    print(f"  JSON:   {output_json}")


if __name__ == "__main__":
    main()
