"""Prompts and answer parsing for the LLM self-selection arms.

2a sees SQL text + schema only. 2b sees exactly that plus the metric table the OR engine ranks
(CLAUDE.md fairness rule 4). Both choose among the SAME options: the original and the
correctness-verified candidates of the cached pool. Static-analysis hints are NOT shown to either.

Options are labelled A, B, C... and shown in a per-(query, repeat) shuffled order, identical for 2a
and 2b within a repeat, so position bias averages out instead of always favouring the original.
Repeat 0 uses the canonical order (original first). The seed is derived from the ids, so a re-run
presents the same orders.
"""
import hashlib
import json
import random
import re

LABELS = "ABCDEFGHIJ"

SYSTEM = """You are an expert MySQL 8.4 performance engineer. You are given several SQL queries that are \
verified to return EXACTLY the same result. Choose the one that will execute fastest on this database.
Answer with JSON only: {"choice": "<one option letter>", "reason": "<one short sentence>"}"""


def option_order(query_id: str, repeat: int, n: int) -> list[int]:
    """Permutation of range(n) (index 0 = the original). Repeat 0 is canonical."""
    idx = list(range(n))
    if repeat > 0:
        seed = int(hashlib.sha256(f"{query_id}:{repeat}".encode()).hexdigest()[:12], 16)
        random.Random(seed).shuffle(idx)
    return idx


def metric_table(options: list[dict], order: list[int]) -> str:
    """The measured/estimated numbers per option, as text. Same data or_decision() ranks."""
    lines = ["option | runtime ms (median, IQR) | rows examined | EXPLAIN cost | EXPLAIN est. rows"]
    for pos, i in enumerate(order):
        m = options[i]["metrics"]
        if m["outcome"] != "ok":
            lines.append(f"{LABELS[pos]} | {m['outcome']} (did not complete) | - | - | -")
        else:
            lines.append(f"{LABELS[pos]} | {m['runtime_ms_median']:.2f}, {m['runtime_ms_iqr']:.2f} | "
                         f"{m['rows_examined']} | {m['est_cost']:.1f} | {m['est_rows']:.0f}")
    return "\n".join(lines)


def build_messages(variant: str, dataset: str, ddl: str, options: list[dict], order: list[int]) -> tuple[list[dict], dict]:
    """variant '2a' or '2b'. options[0] is the original; each option needs id, sql (+ metrics for 2b).
    -> (messages, {label: alt_id})."""
    assert variant in ("2a", "2b")
    label_map = {LABELS[pos]: options[i]["id"] for pos, i in enumerate(order)}
    blocks = []
    for pos, i in enumerate(order):
        tag = "  [this is the original query as written]" if i == 0 else ""
        blocks.append(f"Option {LABELS[pos]}{tag}:\n{options[i]['sql'].strip()}")
    user = f"Database: {dataset}\n\nTable definitions:\n{ddl}\n\n" + "\n\n".join(blocks)
    if variant == "2b":
        user += ("\n\nMeasurements for each option on this server (runtime: median and IQR of 5 timed runs after "
                 "2 warm-ups; rows examined is what MySQL actually read; cost and estimated rows come from EXPLAIN):\n"
                 + metric_table(options, order))
    user += "\n\nWhich option is fastest? Answer with JSON only."
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], label_map


def parse_choice(text: str, label_map: dict) -> tuple[str | None, str]:
    """-> (alt_id, '') on success or (None, error). Strict: a JSON object whose 'choice' is a valid label."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\}", t, re.S)  # an object embedded in chatter
        try:
            data = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict) or "choice" not in data:
        return None, "parse_error: no JSON object with a 'choice' key"
    raw = str(data["choice"]).strip()
    label = re.sub(r"^option\s+", "", raw, flags=re.I).strip().upper()
    if label not in label_map:
        return None, f"invalid_choice: {raw!r} is not one of {sorted(label_map)}"
    return label_map[label], ""
