"""Binomial maximum-likelihood fits of logical failure probability versus rounds."""

from functools import lru_cache
import math

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from scipy.special import xlogy, xlog1py
from scipy.stats import chi2


def failure_probability(rounds, epsilon: float, spam: float = 0.0):
    """Challenge model, optionally allowing preparation/readout offset spam."""
    r = np.asarray(rounds, dtype=float)
    if not 0 <= epsilon <= 0.5 or not 0 <= spam <= 0.5 or np.any(r < 0):
        raise ValueError("epsilon and spam must be in [0, 0.5], and rounds nonnegative")
    if epsilon == 0.5:
        memory = np.where(r == 0, 0.0, 0.5)
    else:
        memory = -0.5 * np.expm1(r * np.log1p(-2 * epsilon))
    return spam + (1 - 2 * spam) * memory


def fit_logical_rate(points: list[dict], include_spam: bool = False) -> dict:
    """Fit counts directly, including zero-failure points; return profile-likelihood CI.

    The reported 95% interval uses the usual asymptotic likelihood-ratio cutoff.
    The optional SPAM fit is a diagnostic: it does not replace the challenge fit.
    """
    if len(points) < 3:
        raise ValueError("At least three round counts are required for the fit")
    rounds = np.array([p["rounds"] for p in points], dtype=float)
    shots = np.array([p["shots"] for p in points], dtype=float)
    failures = np.array([p["failures"] for p in points], dtype=float)
    if len(set(rounds)) != len(rounds) or np.any(rounds < 1) or np.any(rounds % 1):
        raise ValueError("Round counts must be distinct positive integers")
    if np.any(shots <= 0) or np.any(shots % 1) or np.any(failures % 1) or np.any(failures < 0) or np.any(failures > shots):
        raise ValueError("Invalid binomial shot/failure counts")

    def nll(epsilon, spam):
        p = failure_probability(rounds, epsilon, spam)
        return float(-np.sum(xlogy(failures, p) + xlog1py(shots - failures, -p)))

    @lru_cache(maxsize=1024)
    def profile(epsilon):
        if not include_spam:
            return nll(epsilon, 0), 0.0
        result = minimize_scalar(lambda b: nll(epsilon, b), bounds=(0, 0.5), method="bounded", options={"xatol": 1e-10})
        candidates = [(nll(epsilon, 0), 0.0), (nll(epsilon, 0.5), 0.5), (result.fun, result.x)]
        return min(candidates)

    result = minimize_scalar(lambda e: profile(float(e))[0], bounds=(0, 0.5), method="bounded", options={"xatol": 1e-10})
    candidates = [(profile(e)[0], e, profile(e)[1]) for e in [0.0, 0.5, float(result.x)]]
    best_nll, epsilon, spam = min(candidates)
    cutoff = best_nll + chi2.ppf(0.95, 1) / 2

    def residual(e):
        return profile(float(e))[0] - cutoff

    lower = 0.0 if epsilon == 0 or residual(0.0) <= 0 else brentq(residual, 0.0, epsilon)
    upper = 0.5 if epsilon == 0.5 or residual(0.5) <= 0 else brentq(residual, epsilon, 0.5)
    saturated_nll = -float(np.sum(xlogy(failures, failures / shots) + xlog1py(shots - failures, -failures / shots)))
    deviance = max(0.0, 2 * (best_nll - saturated_nll))
    degrees_of_freedom = len(points) - (2 if include_spam else 1)
    p_value = float(chi2.sf(deviance, degrees_of_freedom))
    return {
        "model": "spam_offset" if include_spam else "challenge",
        "epsilon_per_round": float(epsilon),
        "epsilon_ci95": [float(lower), float(upper)],
        "spam_offset": float(spam),
        "deviance": float(deviance),
        "degrees_of_freedom": degrees_of_freedom,
        "goodness_of_fit_p_value": p_value,
        "model_mismatch_flag": p_value < 0.05,
        "confidence_method": "Approximate 95% profile-likelihood interval",
        "note": "The challenge fit assumes no preparation/readout offset; inspect the SPAM diagnostic and goodness of fit.",
    }
