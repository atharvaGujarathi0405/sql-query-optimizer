"""AHP (criteria weights) and TOPSIS (alternative ranking) in NumPy.

AHP: weights are the principal eigenvector of a reciprocal pairwise matrix;
the consistency ratio CR = CI / RI must be below 0.1 or the judgments are
rejected (`load_ahp` raises). TOPSIS: vector-normalise, weight, measure the
distance to the ideal and anti-ideal point, rank by relative closeness.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

# Saaty's random consistency index by matrix size.
RI = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49}
CR_LIMIT = 0.1


@dataclass(frozen=True)
class AHPResult:
    weights: np.ndarray
    lambda_max: float
    ci: float
    cr: float


def ahp(matrix) -> AHPResult:
    A = np.asarray(matrix, dtype=float)
    n = A.shape[0]
    if A.ndim != 2 or A.shape[1] != n or n not in RI:
        raise ValueError(f"pairwise matrix must be square with 1..10 rows, got shape {A.shape}")
    if (A <= 0).any() or not np.allclose(A * A.T, 1.0):
        raise ValueError("pairwise matrix must be positive and reciprocal (a_ij * a_ji = 1)")
    vals, vecs = np.linalg.eig(A)
    k = int(np.argmax(vals.real))
    w = np.abs(vecs[:, k].real)
    w = w / w.sum()
    lam = float(vals[k].real)
    ci = (lam - n) / (n - 1) if n > 2 else 0.0
    cr = ci / RI[n] if RI[n] else 0.0
    return AHPResult(w, lam, ci, cr)


def load_ahp(path: Path) -> tuple[list[str], list[bool], AHPResult]:
    """Read config/ahp.yaml -> (criteria, benefit flags, AHP result). Raises if CR >= 0.1."""
    cfg = yaml.safe_load(Path(path).read_text())
    criteria = cfg["criteria"]
    idx = {c: i for i, c in enumerate(criteria)}
    A = np.ones((len(criteria), len(criteria)))
    for a, b, v in cfg["pairwise"]:  # "a is v times as important as b"
        A[idx[a], idx[b]] = v
        A[idx[b], idx[a]] = 1.0 / v
    res = ahp(A)
    if res.cr >= CR_LIMIT:
        raise ValueError(f"AHP judgments in {path} are inconsistent: CR={res.cr:.4f} >= {CR_LIMIT}")
    return criteria, [cfg["direction"][c] == "benefit" for c in criteria], res


def topsis(X, weights, benefit) -> np.ndarray:
    """Relative closeness in [0, 1] per alternative (row of X); higher is better.

    X: alternatives x criteria. benefit[j] True = larger is better, False = cost.
    A constant column (zero spread) contributes nothing, as it should.
    """
    X = np.asarray(X, dtype=float)
    w = np.asarray(weights, dtype=float)
    norm = np.sqrt((X**2).sum(axis=0))
    norm[norm == 0] = 1.0
    V = X / norm * w
    b = np.asarray(benefit, dtype=bool)
    ideal = np.where(b, V.max(axis=0), V.min(axis=0))
    anti = np.where(b, V.min(axis=0), V.max(axis=0))
    d_plus = np.sqrt(((V - ideal) ** 2).sum(axis=1))
    d_minus = np.sqrt(((V - anti) ** 2).sum(axis=1))
    total = d_plus + d_minus
    # All alternatives identical -> no preference; 0.5 for everyone, tie-break decides.
    return np.where(total > 0, d_minus / np.where(total > 0, total, 1.0), 0.5)


def rank(closeness, eps: float = 1e-12) -> list[int]:
    """Indices best-first. Ties (within eps) keep input order, so put the original query at index 0
    and it wins ties: a rewrite must be strictly better to be chosen."""
    c = np.asarray(closeness, dtype=float)
    return sorted(range(len(c)), key=lambda i: (-round(c[i] / eps) if eps else -c[i], i))
