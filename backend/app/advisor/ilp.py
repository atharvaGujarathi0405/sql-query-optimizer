"""Index selection as an ILP (PuLP + CBC), and the greedy heuristic it is compared with.

Inputs are MEASURED: benefit[(query, index)] = ms saved on that query by that index alone (0 when the
saving is within noise), size[index] in MB. A query is served by the best selected index (max, not sum:
two indexes that both fix the same query do not double the gain), so:

    max  sum_{q,i} b_qi y_qi  -  sum_i penalty_i x_i  -  eps * sum_i size_i x_i
    s.t. sum_i y_qi <= 1 for each q;  y_qi <= x_i;  sum_i size_i x_i <= budget;  sum_i x_i <= max_indexes

penalty_i is the measured slowdown (ms, beyond noise) that index i causes on other queries of the workload: an
index that fixes one query and breaks another is charged for it (conservatively: the sum of its regressions).

Both selectors get the same table, budget and cap (fairness: same pool, same measurements).
"""
import pulp


def predicted(chosen, benefit, penalty=None) -> float:
    """Net gain the tables predict for a chosen set: per query the best chosen index, minus the chosen indexes' penalties."""
    best: dict = {}
    for (q, i), b in benefit.items():
        if i in chosen:
            best[q] = max(best.get(q, 0.0), b)
    return sum(best.values()) - sum((penalty or {}).get(i, 0.0) for i in chosen)


def solve_ilp(benefit: dict, size: dict, budget_mb: float, max_indexes: int, penalty: dict | None = None) -> dict:
    penalty = penalty or {}
    prob = pulp.LpProblem("index_selection", pulp.LpMaximize)
    x = {i: pulp.LpVariable(f"x_{n}", cat="Binary") for n, i in enumerate(sorted(size))}
    y = {k: pulp.LpVariable(f"y_{n}", cat="Binary") for n, k in enumerate(sorted(k for k, b in benefit.items() if b > 0))}
    eps = 1e-6  # tie-break toward smaller sets; far below any real benefit (ms)
    prob += (pulp.lpSum(benefit[k] * v for k, v in y.items()) - pulp.lpSum(penalty.get(i, 0.0) * v for i, v in x.items())
             - eps * pulp.lpSum(size[i] * v for i, v in x.items()))
    for q in {q for q, _ in y}:
        prob += pulp.lpSum(v for (qq, _), v in y.items() if qq == q) <= 1
    for (q, i), v in y.items():
        prob += v <= x[i]
    prob += pulp.lpSum(size[i] * v for i, v in x.items()) <= budget_mb
    prob += pulp.lpSum(x.values()) <= max_indexes
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    status = pulp.LpStatus[prob.status]
    chosen = sorted(i for i, v in x.items() if (v.value() or 0) > 0.5) if status == "Optimal" else []
    return {"chosen": chosen, "predicted_ms": predicted(set(chosen), benefit, penalty), "solver": f"CBC {status}"}


def solve_greedy(benefit: dict, size: dict, budget_mb: float, max_indexes: int, penalty: dict | None = None) -> dict:
    """Repeatedly add the index with the best marginal gain per MB that still fits; stop when none gains."""
    chosen: list = []
    while len(chosen) < max_indexes:
        used = sum(size[i] for i in chosen)
        base = predicted(set(chosen), benefit, penalty)
        options = [((predicted(set(chosen) | {i}, benefit, penalty) - base) / max(size[i], 1e-9), i)
                   for i in sorted(size) if i not in chosen and used + size[i] <= budget_mb]
        options = [o for o in options if o[0] > 0]
        if not options:
            break
        chosen.append(max(options, key=lambda o: (o[0], o[1]))[1])
    return {"chosen": sorted(chosen), "predicted_ms": predicted(set(chosen), benefit, penalty), "solver": "greedy gain per MB"}
