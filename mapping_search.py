"""Simulated annealing over valid placements with a cached Monte Carlo objective."""

import math
import numpy as np


def simulated_annealing(cost, candidates: int, steps: int = 200, seed: int = 42, start: int = 0,
                        initial_temperature: float = 0.02, final_temperature: float = 0.0002):
    """Lower cost is better. cost(i) may cache a fixed Monte Carlo screening batch.

    Proposals select another fully valid placement, so no intermediate placement
    is executed or accepted with a non-native edge. Independent holdout shots
    must be used after choosing a candidate to avoid reporting selection noise.
    """
    if candidates < 1 or not 0 <= start < candidates or steps < 1:
        raise ValueError("Invalid candidate count, starting index or step count")
    if initial_temperature <= 0 or final_temperature <= 0:
        raise ValueError("Temperatures must be positive")
    rng = np.random.default_rng(seed)
    current = best = start
    current_cost = best_cost = float(cost(start))
    trace = []
    for step in range(steps):
        temperature = initial_temperature * (final_temperature / initial_temperature) ** (step / max(1, steps - 1))
        proposed = int(rng.integers(candidates - 1)) if candidates > 1 else current
        if candidates > 1 and proposed >= current:
            proposed += 1
        proposed_cost = float(cost(proposed))
        delta = proposed_cost - current_cost
        accepted = delta <= 0 or rng.random() < math.exp(-delta / temperature)
        if accepted:
            current, current_cost = proposed, proposed_cost
        if current_cost < best_cost:
            best, best_cost = current, current_cost
        trace.append({"step": step, "temperature": temperature, "proposed": proposed,
                      "accepted": bool(accepted), "current": current, "cost": current_cost, "best": best})
    return {"best_index": best, "best_screening_cost": best_cost, "trace": trace, "seed": seed}
