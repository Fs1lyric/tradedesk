"""Overfitting statistics: deflated Sharpe ratio and CSCV probability of backtest overfitting.

References: Bailey & Lopez de Prado, "The Deflated Sharpe Ratio" (2014) and
Bailey, Borwein, Lopez de Prado & Zhu, "The Probability of Backtest Overfitting" (2016).
Sharpe ratios here are per period (not annualized).
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist

import numpy as np

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def expected_max_sharpe(n_trials: int, trial_sharpe_variance: float) -> float:
    """Expected maximum Sharpe among n_trials unskilled strategies."""
    if n_trials <= 1 or trial_sharpe_variance <= 0:
        return 0.0
    a = _N.inv_cdf(1 - 1 / n_trials)
    b = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(trial_sharpe_variance) * ((1 - EULER_GAMMA) * a + EULER_GAMMA * b)


def deflated_sharpe(returns: np.ndarray, n_trials: int, trial_sharpe_variance: float) -> float:
    """Probability that the true Sharpe exceeds the best expected from luck alone."""
    r = np.asarray(returns, dtype=float)
    t = len(r)
    sd = r.std(ddof=1) if t > 1 else 0.0
    if t < 3 or sd == 0:
        return 0.0
    sr = r.mean() / sd
    z = (r - r.mean()) / r.std()
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))
    sr0 = expected_max_sharpe(n_trials, trial_sharpe_variance)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))


def _sharpe_cols(m: np.ndarray) -> np.ndarray:
    sd = m.std(axis=0, ddof=1)
    return np.where(sd > 0, m.mean(axis=0) / np.where(sd > 0, sd, 1), 0.0)


def probability_of_backtest_overfitting(
    returns: np.ndarray, n_splits: int = 16, max_combinations: int = 5000, seed: int = 0
) -> float:
    """CSCV: share of train/test splits where the in-sample best config ranks below the
    out-of-sample median. `returns` is T x N (periods x configurations)."""
    m = np.asarray(returns, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise ValueError("need a T x N matrix with N >= 2 configurations")
    if n_splits % 2 or n_splits < 2:
        raise ValueError("n_splits must be even")
    t = (m.shape[0] // n_splits) * n_splits
    blocks = np.array_split(m[:t], n_splits)
    combos = list(itertools.combinations(range(n_splits), n_splits // 2))
    if len(combos) > max_combinations:
        rng = np.random.default_rng(seed)
        combos = [combos[i] for i in rng.choice(len(combos), max_combinations, replace=False)]
    n = m.shape[1]
    below = 0
    for train_idx in combos:
        test_idx = [i for i in range(n_splits) if i not in train_idx]
        train = np.vstack([blocks[i] for i in train_idx])
        test = np.vstack([blocks[i] for i in test_idx])
        best = int(np.argmax(_sharpe_cols(train)))
        oos = _sharpe_cols(test)
        rank = (np.argsort(np.argsort(oos))[best] + 1) / (n + 1)  # relative rank in (0, 1)
        logit = math.log(rank / (1 - rank))
        below += logit <= 0
    return below / len(combos)
