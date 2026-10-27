"""SQLGlot parsing and anti-pattern detection.

Works from SQL text (+ optional schema), never from the registry's labels, so the
hints it feeds to the candidate generator cannot leak ground truth. Detection is
heuristic and syntactic: a finding is a lead, not a verdict. Names match the tags
used in data/antipatterns/*.sql where one exists.
"""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import build_scope

DEEP_OFFSET = 10_000
_ARITH = (exp.Add, exp.Sub, exp.Mul, exp.Div)
_COMPARE = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
_STRING_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext"}


@dataclass(frozen=True)
class Finding:
    pattern: str
    detail: str


def parse(sql: str) -> exp.Expression:
    return sqlglot.parse_one(sql, read="mysql")


def tables_in(sql: str) -> list[str]:
    """Base tables referenced (CTE names excluded), in order of first appearance."""
    tree = parse(sql)
    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    seen: list[str] = []
    for t in tree.find_all(exp.Table):
        if t.name and t.name not in ctes and t.name not in seen:
            seen.append(t.name)
    return seen


def _has_column(node) -> bool:
    return node is not None and node.find(exp.Column) is not None


def _where_conditions(tree):
    for w in tree.find_all(exp.Where):
        yield w.this


def _is_wrapped(side) -> bool:
    return isinstance(side, (exp.Func, exp.Cast) + _ARITH) and not isinstance(side, exp.AggFunc) and _has_column(side)


def detect(sql: str, schema: dict | None = None) -> list[Finding]:
    """schema: {table: {column: type}} (see load_schema). Enables correlated-subquery detection for
    unqualified columns and implicit-cast detection."""
    tree = parse(sql)
    q = tree
    if schema:
        try:
            q = qualify(tree.copy(), schema=schema, dialect="mysql", validate_qualify_columns=False)
        except Exception:
            q = tree
    out: list[Finding] = []

    # select-star: bare * or t.* in a select list; COUNT(*) and EXISTS(SELECT *) are not the anti-pattern
    for sel in tree.find_all(exp.Select):
        if isinstance(sel.parent, exp.Exists):
            continue
        for e in sel.expressions:
            if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
                out.append(Finding("select-star", "SELECT * returns every column"))

    # function / arithmetic on a column inside a WHERE comparison (non-sargable)
    for cond in _where_conditions(tree):
        for cmp_ in cond.find_all(*_COMPARE, exp.Between, exp.In):
            sides = [cmp_.this] + ([cmp_.args.get("expression")] if cmp_.args.get("expression") is not None else [])
            for i, side in enumerate(sides):
                other = sides[1 - i] if len(sides) == 2 else None
                if _is_wrapped(side) and not _has_column(other):
                    out.append(Finding("function-on-column", f"{side.sql(dialect='mysql')[:80]} wraps a column in a comparison"))

    # leading wildcard LIKE
    for like in tree.find_all(exp.Like):
        pat = like.expression
        if isinstance(pat, exp.Literal) and pat.is_string and pat.this[:1] in ("%", "_"):
            out.append(Finding("leading-wildcard-like", f"LIKE '{pat.this}' cannot use an index range"))

    # NOT IN (subquery)
    for n in tree.find_all(exp.Not):
        if isinstance(n.this, exp.In) and n.this.args.get("query") is not None:
            out.append(Finding("not-in", "NOT IN (subquery) is NULL-sensitive and often slower than NOT EXISTS / anti-join"))

    # correlated subquery (scalar, EXISTS, IN ...)
    try:
        root = build_scope(q)
        for scope in (root.traverse() if root else []):
            if scope.is_subquery and scope.external_columns:
                out.append(Finding("correlated-subquery", "subquery references the outer query and runs per outer row"))
    except Exception:
        pass

    # scalar subquery in the SELECT list
    for sel in tree.find_all(exp.Select):
        if any(isinstance(e.unalias(), exp.Subquery) for e in sel.expressions):
            out.append(Finding("subquery-in-select", "subquery in the select list runs per output row"))

    # OR over different columns
    for w in tree.find_all(exp.Where):
        for or_ in w.find_all(exp.Or):
            if isinstance(or_.parent, exp.Or):
                continue  # take only the outermost OR of a chain
            branches, stack = [], [or_]
            while stack:
                n = stack.pop()
                (stack.extend([n.this, n.expression]) if isinstance(n, exp.Or) else branches.append(n))
            cols = {frozenset(c.name for c in b.find_all(exp.Column)) for b in branches}
            if len(cols) > 1:
                out.append(Finding("or-across-columns", "OR over different columns prevents a single index range"))

    # DISTINCT (whether it is redundant needs key knowledge; this is only a lead)
    for sel in tree.find_all(exp.Select):
        if sel.args.get("distinct") and not isinstance(sel.parent, exp.Union):
            out.append(Finding("distinct", "SELECT DISTINCT: check whether duplicates can actually occur"))

    # HAVING without any aggregate
    for h in tree.find_all(exp.Having):
        if not h.find(exp.AggFunc):
            out.append(Finding("having-instead-of-where", "HAVING condition has no aggregate; WHERE filters earlier"))

    # UNION (distinct) rather than UNION ALL
    for u in tree.find_all(exp.Union):
        if u.args.get("distinct", True):
            out.append(Finding("union-vs-union-all", "UNION de-duplicates; UNION ALL is cheaper if branches are disjoint"))

    # deep OFFSET
    for off in tree.find_all(exp.Offset):
        if isinstance(off.expression, exp.Literal) and int(off.expression.this) >= DEEP_OFFSET:
            out.append(Finding("deep-offset", f"OFFSET {off.expression.this} reads and discards that many rows"))

    out += _implicit_casts(q, schema)

    seen, uniq = set(), []
    for f in out:  # one entry per (pattern, detail)
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def _implicit_casts(tree, schema) -> list[Finding]:
    """String column compared with a numeric literal (the per-row-cast direction)."""
    if not schema:
        return []
    col_type = {}
    for t, cols in schema.items():
        for c, ty in cols.items():
            col_type.setdefault((t.lower(), c.lower()), str(ty).lower().split("(")[0])
    alias_to_table = {(t.alias_or_name or "").lower(): t.name.lower() for t in tree.find_all(exp.Table)}
    found = []
    for cmp_ in tree.find_all(*_COMPARE):
        for col, lit in ((cmp_.this, cmp_.expression), (cmp_.expression, cmp_.this)):
            if isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and not lit.is_string:
                table = alias_to_table.get((col.table or "").lower(), (col.table or "").lower())
                ty = col_type.get((table, col.name.lower()))
                if ty in _STRING_TYPES:
                    found.append(Finding("implicit-cast", f"string column {col.name} compared with number {lit.this}"))
    return found


def load_schema(conn, dataset: str) -> dict:
    """{table: {column: data_type}} for one dataset, read as the reader user."""
    cur = conn.cursor()
    cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (dataset,))
    schema: dict = {}
    for t, c, ty in cur.fetchall():
        schema.setdefault(t, {})[c] = ty
    return schema


def schema_text(conn, dataset: str, tables: list[str]) -> str:
    """CREATE TABLE statements (incl. indexes) for the given tables: the schema context an LLM sees."""
    cur = conn.cursor()
    parts = []
    for t in tables:
        cur.execute(f"SHOW CREATE TABLE `{dataset}`.`{t}`")
        row = cur.fetchone()
        if row:
            parts.append(row[1] + ";")
    return "\n\n".join(parts)
