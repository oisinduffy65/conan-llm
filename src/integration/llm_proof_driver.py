#!/usr/bin/env python3
"""
How to use:
python integration/llm_proof_driver.py \\
    --premise "P → Q" \\
    --premise "P" \\
    --conclusion "Q" \\
    --model gpt-4.1-mini
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List


OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com")
REQUEST_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT", "180"))


SYSTEM_PROMPT = textwrap.dedent(
    """\
    You are assisting with automated natural deduction proofs for first-order logic.
    Syntax rules:
    - Predicates use uppercase identifiers; terms use lowercase (optionally with digits).
    - Quantifiers and connectives: ∀, ∃, ¬, ∧, ∨, →. Precedence: unary > ∧/∨ > →.
    - Columns are: line_number (implicit), formula, rule. Rules must match the proof checker:
      Premise, Ass., Fresh, ∧i, ∧e₁, ∧e₂, ∨i₁, ∨i₂, ∨e, →i, →e, ¬i, ¬e, ¬¬i, ¬¬e,
      ⊥e, ∀i, ∀e, ∃i, ∃e, =i, =e, MT, PBC, LEM, Copy, etc.
    - References for rules go in the rule column after the mnemonic, e.g. '→e 1,2'.
    - Include every premise as the first proof lines with rule 'Premise'.
    Respond with pure JSON matching:
      {"proof_steps": [{"formula": "...", "rule": "..."}, ...]}
    Do not add commentary or markdown.
    """
)


@dataclass(frozen=True)
class ProofStep:
    formula: str
    rule: str


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask an LLM for a proof and verify it.")
    parser.add_argument(
        "--premise",
        action="append",
        default=[],
        help="Premise formula (you can repeat this flag).",
    )
    parser.add_argument(
        "--conclusion",
        required=True,
        help="Target conclusion formula.",
    )
    parser.add_argument(
        "--model",
        default="gpt-4.1-mini",
        help="OpenAI model name (default: %(default)s).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1,
        help="Sampling temperature for the LLM.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the LLM call; expect proof JSON on stdin.",
    )
    return parser.parse_args(argv)


def call_llm(model: str, temperature: float, premises: List[str], conclusion: str) -> List[ProofStep]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required.")

    prompt = build_user_prompt(premises, conclusion)

    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        f"{OPENAI_API_BASE}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        message = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed: {err.code} {message}") from err
    except urllib.error.URLError as err:
        raise RuntimeError(f"OpenAI request failed: {err.reason}") from err

    content = json.loads(body)["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    steps = [ProofStep(formula=item["formula"], rule=item.get("rule", "")) for item in parsed["proof_steps"]]
    return steps


def build_user_prompt(premises: List[str], conclusion: str) -> str:
    lines = ["Find a proof that derives the conclusion from the premises."]
    if premises:
        lines.append("Premises:")
        for idx, premise in enumerate(premises, start=1):
            lines.append(f"  {idx}. {premise}")
    else:
        lines.append("No premises (empty context).")
    lines.append(f"Conclusion: {conclusion}")
    lines.append("")
    lines.append(
        "Return a complete sequence of proof lines, including the premises as the first lines."
    )
    lines.append("Ensure references match line numbers starting at 1.")
    return "\n".join(lines)


def load_steps_from_stdin() -> List[ProofStep]:
    data = sys.stdin.read()
    parsed = json.loads(data)
    return [ProofStep(formula=item["formula"], rule=item.get("rule", "")) for item in parsed["proof_steps"]]


def call_proof_checker(premises: List[str], conclusion: str, steps: List[ProofStep]) -> dict:
    payload_lines = [f"CONCLUSION|{conclusion}"]
    for premise in premises:
        payload_lines.append(f"PREMISE|{premise}")
    for step in steps:
        payload_lines.append(f"STEP|{step.formula}||{step.rule}")
    payload = "\n".join(payload_lines) + "\n"

    # Ensure both project root (.) and source tree (src) are on the classpath,
    # since classes may be compiled either into the root or remain under src/.
    classpath = os.pathsep.join((".", "src"))
    proc = subprocess.run(
        ["java", "-cp", classpath, "integration.ProofVerifier"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Proof verifier exited with code {proc.returncode}: {proc.stderr.strip()}"
        )

    output = proc.stdout.strip()
    if not output:
        raise RuntimeError("Proof verifier produced no output.")
    try:
        return json.loads(output)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Failed to parse verifier output '{output}': {err}") from err


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    if args.dry_run:
        steps = load_steps_from_stdin()
    else:
        steps = call_llm(args.model, args.temperature, args.premise, args.conclusion)

    checker_result = call_proof_checker(args.premise, args.conclusion, steps)

    print_table(steps)
    print()
    print(json.dumps({"valid": checker_result.get("valid", False), "steps": [step.__dict__ for step in steps]}))
    if not checker_result.get("valid", False):
        error = checker_result.get("error")
        if error:
            print(f"Proof checker error: {error}", file=sys.stderr)
        return 1
    return 0


def print_table(steps: List[ProofStep]) -> None:
    headers = ("Step", "Formula", "Justification")
    rows = []
    for idx, step in enumerate(steps, start=1):
        rows.append((str(idx), step.formula, step.rule))

    widths = [len(column) for column in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    def format_row(row: tuple[str, str, str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(row, widths))

    print(format_row(headers))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(format_row(row))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
