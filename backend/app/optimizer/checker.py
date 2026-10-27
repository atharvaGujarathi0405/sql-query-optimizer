"""Correctness checker (CLAUDE.md fairness rule 3).

A rewrite is valid only if it returns the same result set as the original:
row count first, then an order-insensitive MULTISET comparison (duplicates
count), or an exact sequence comparison when the original has a top-level
ORDER BY. Column names are not compared (an alias is not a different result);
column count is.

Numbers compare by value across int/Decimal/float (1, 1.0, Decimal('1.00') are
equal), but a string never equals a number, and NULL equals only NULL
(not '', not 0). Floats are compared exactly: the datasets use DECIMAL, so no
tolerance is applied. That is a known limit if DOUBLE columns are ever added.

Failures are returned with a reason, never dropped; the caller stores them.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal

import sqlglot
from sqlglot import exp

from app.optimizer.execution import QueryFailed, QueryTimeout, run

_NULL = ("<NULL>",)  # sentinel distinct from every real value, including the string "NULL"


@dataclass
class CheckResult:
    valid: bool
    reason: str  # ok | not_select | exec_error | timeout | col_count_mismatch | row_count_mismatch
    #              | rows_mismatch | order_mismatch | original_failed
    detail: str = ""
    original_rows: int | None = None
    candidate_rows: int | None = None
    order_sensitive: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Original:
    rows: list[tuple] | None
    ncols: int | None
    order_sensitive: bool
    error: str = ""


def _norm(v):
    if v is None:
        return _NULL
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, (int, Decimal)):
        return Decimal(v).normalize()
    if isinstance(v, float):
        return Decimal(repr(v)).normalize()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return v  # str, date, datetime, timedelta


def _norm_row(row) -> tuple:
    return tuple(_norm(v) for v in row)


def is_order_sensitive(sql: str) -> bool:
    """True when the outermost query has an ORDER BY (an ORDER BY inside a subquery does not count)."""
    return bool(sqlglot.parse_one(sql, read="mysql").args.get("order"))


def read_only_select_problem(sql: str) -> str | None:
    """None if `sql` is exactly one SELECT/UNION/WITH-SELECT statement, else why not."""
    try:
        stmts = [s for s in sqlglot.parse(sql, read="mysql") if s is not None]
    except sqlglot.errors.ParseError as e:
        return f"does not parse: {str(e)[:120]}"
    if len(stmts) != 1:
        return f"expected one statement, got {len(stmts)}"
    if not isinstance(stmts[0], (exp.Select, exp.SetOperation)):
        return f"not a SELECT: {type(stmts[0]).__name__}"
    if stmts[0].find(exp.Into):
        return "SELECT ... INTO is not allowed"
    return None


def compare_results(orig: list[tuple], cand: list[tuple], order_sensitive: bool,
                    orig_ncols: int | None = None, cand_ncols: int | None = None) -> CheckResult:
    """Pure comparison, no database. Order of checks: columns, row count, then rows."""
    base = dict(original_rows=len(orig), candidate_rows=len(cand), order_sensitive=order_sensitive)
    if orig_ncols is not None and cand_ncols is not None and orig_ncols != cand_ncols:
        return CheckResult(False, "col_count_mismatch", f"{orig_ncols} vs {cand_ncols} columns", **base)
    if len(orig) != len(cand):
        return CheckResult(False, "row_count_mismatch", f"{len(orig)} vs {len(cand)} rows", **base)
    a, b = [_norm_row(r) for r in orig], [_norm_row(r) for r in cand]
    if order_sensitive:
        if a == b:
            return CheckResult(True, "ok", **base)
        same_rows = Counter(a) == Counter(b)
        i = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)  # raw rows shown below, not normalized ones
        return CheckResult(False, "order_mismatch" if same_rows else "rows_mismatch",
                           f"first difference at row {i}: {orig[i]!r} vs {cand[i]!r}", **base)
    ca, cb = Counter(a), Counter(b)
    if ca == cb:
        return CheckResult(True, "ok", **base)
    raw_a, raw_b = dict(zip(reversed(a), reversed(orig))), dict(zip(reversed(b), reversed(cand)))  # normalized -> raw, for messages
    only_orig, only_cand = list((ca - cb).elements()), list((cb - ca).elements())
    show = lambda rows, raw: repr(raw[rows[0]]) if rows else "-"
    return CheckResult(False, "rows_mismatch",
                       f"{len(only_orig)} row(s) only in original, e.g. {show(only_orig, raw_a)}; "
                       f"{len(only_cand)} only in candidate, e.g. {show(only_cand, raw_b)}", **base)


def fetch_original(cur, sql: str) -> Original:
    """Run the original once; every candidate of this query is compared against this."""
    order = is_order_sensitive(sql)
    try:
        rows, ncols = run(cur, sql)
        return Original(rows, ncols, order)
    except (QueryTimeout, QueryFailed) as e:
        return Original(None, None, order, f"{type(e).__name__}: {e}")


def check_candidate(cur, orig: Original, candidate_sql: str) -> CheckResult:
    if orig.rows is None:
        return CheckResult(False, "original_failed", orig.error, order_sensitive=orig.order_sensitive)
    problem = read_only_select_problem(candidate_sql)
    if problem:
        return CheckResult(False, "not_select", problem, original_rows=len(orig.rows),
                           order_sensitive=orig.order_sensitive)
    try:
        rows, ncols = run(cur, candidate_sql)
    except QueryTimeout as e:
        return CheckResult(False, "timeout", str(e), original_rows=len(orig.rows), order_sensitive=orig.order_sensitive)
    except QueryFailed as e:
        return CheckResult(False, "exec_error", str(e)[:300], original_rows=len(orig.rows),
                           order_sensitive=orig.order_sensitive)
    return compare_results(orig.rows, rows, orig.order_sensitive, orig.ncols, ncols)
