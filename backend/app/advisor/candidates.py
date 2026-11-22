"""Candidate indexes for a workload, from SQLGlot's parse of its queries.

Per table and query: columns compared with = / IN (or joined) are `eq`, columns under < > BETWEEN or a
prefix LIKE are `range`, GROUP BY / ORDER BY columns are `order`. Candidates are each single column plus
composites eq+range and eq+order (eq columns first), at most `max_cols` wide. Columns inside functions
(YEAR(col)) are not sargable and are skipped. A candidate already covered by the leading columns of an
existing index (the primary key included) is dropped.
A candidate that MySQL would absorb an implicit foreign-key index into is caught at run time (advise.indexes_created). Ranked: predicate-driven before GROUP/ORDER-only singles, then by how many queries want it, then by name.
"""
import sqlglot
from sqlglot import exp

RANGE = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)
ADV_PREFIX = "adv_"  # every experimental index carries this prefix so a leftover is recognisable and removable


def schema_info(conn, dataset: str):
    """-> (tables {lower: (actual, {col_lower: col})}, existing {table_lower: [column tuples]})"""
    cur = conn.cursor()
    cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=%s", (dataset,))
    tables: dict = {}
    for t, c in cur.fetchall():
        tables.setdefault(t.lower(), (t, {}))[1][c.lower()] = c
    cur.execute("SELECT table_name, index_name, seq_in_index, column_name FROM information_schema.statistics "
                "WHERE table_schema=%s AND index_name NOT LIKE 'adv\\_%%' ORDER BY table_name, index_name, seq_in_index", (dataset,))
    idx: dict = {}
    for t, i, _, c in cur.fetchall():
        idx.setdefault((t.lower(), i), []).append(c.lower())
    existing: dict = {}
    for (t, _), cols in idx.items():
        existing.setdefault(t, []).append(tuple(cols))
    return tables, existing


def analyze(sql: str, tables: dict) -> dict:
    """-> {table_lower: {"eq": [...], "range": [...], "order": [...]}} (lower-case columns, first-seen order)."""
    tree = sqlglot.parse_one(sql, dialect="mysql")
    # ponytail: one alias map for the whole statement; a re-used alias across subqueries resolves to the last table.
    alias = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table) if t.name.lower() in tables}
    used = set(alias.values())
    use: dict = {t: {"eq": [], "range": [], "order": []} for t in used}

    def resolve(node):
        if not isinstance(node, exp.Column):
            return None
        if node.table:
            t = alias.get(node.table.lower())
        else:
            owners = [t for t in used if node.name.lower() in tables[t][1]]
            t = owners[0] if len(owners) == 1 else None  # ambiguous or unknown: skip
        return (t, node.name.lower()) if t and node.name.lower() in tables[t][1] else None

    def add(kind, node):
        r = resolve(node)
        if r and r[1] not in use[r[0]][kind]:
            use[r[0]][kind].append(r[1])

    for pred in tree.find_all(exp.Where, exp.Join):
        for n in pred.find_all(exp.EQ, exp.In, exp.Like, *RANGE):
            if isinstance(n, exp.EQ):
                add("eq", n.left), add("eq", n.right)
            elif isinstance(n, exp.In):
                add("eq", n.this)
            elif isinstance(n, exp.Like):
                lit = n.expression
                if isinstance(lit, exp.Literal) and lit.is_string and not lit.name.startswith(("%", "_")):
                    add("range", n.this)  # prefix LIKE is a range scan; a leading wildcard is not sargable
            else:
                add("range", n.this)
    for n in tree.find_all(exp.Group, exp.Order):
        for e in n.expressions:
            add("order", e.this if isinstance(e, exp.Ordered) else e)
    return use


def _combos(u: dict, max_cols: int):
    """(columns, order_only) pairs for one table in one query. order_only singles come from GROUP BY / ORDER BY alone."""
    for kind in ("eq", "range", "order"):
        for c in u[kind]:
            yield (c,), kind == "order" and c not in u["eq"] + u["range"]
    eq = u["eq"][: max_cols - 1]
    for tail in (u["range"][:1], u["order"][:1]):
        if eq and tail and tail[0] not in eq:
            yield tuple(eq) + tuple(tail), False
    if len(u["eq"]) >= 2:
        yield tuple(u["eq"][:max_cols]), False


def candidates(conn, dataset: str, sqls: dict[str, str], max_candidates: int, max_cols: int) -> tuple[list[dict], dict]:
    """-> (candidates, tables_per_query). Each candidate: {table, cols (actual names), wanted_by: [query ids]}."""
    tables, existing = schema_info(conn, dataset)
    want: dict = {}
    touched: dict = {}
    for qid, sql in sqls.items():
        try:
            use = analyze(sql, tables)
        except sqlglot.errors.SqlglotError:
            continue
        touched[qid] = sorted(tables[t][0] for t in use)
        for t, u in use.items():
            for cols, order_only in set(_combos(u, max_cols)):
                if any(e[: len(cols)] == cols for e in existing.get(t, [])):
                    continue  # an existing index already serves this prefix
                w = want.setdefault((t, cols), [[], True])
                w[0].append(qid)
                w[1] = w[1] and order_only
    # predicate-driven candidates first (order-only singles are the weakest guess), then by how many queries want them
    ranked = sorted(want.items(), key=lambda kv: (kv[1][1], -len(kv[1][0]), kv[0]))[:max_candidates]
    return ([{"table": tables[t][0], "cols": [tables[t][1][c] for c in cols], "wanted_by": sorted(q)} for (t, cols), (q, _) in ranked],
            touched)
