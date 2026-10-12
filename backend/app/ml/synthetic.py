"""Reproducible SYNTHETIC access-log generator.

Real access logs are rarely available for a demo, so this module simulates
a population of files whose access intensity follows plausible patterns:

* popularity that decays after creation (most files cool down over time),
* periodic files (e.g. monthly reports re-read at month start),
* occasional "revival" bursts (an old file becomes relevant again),
* type-dependent base rates and sizes.

Everything produced here is labelled ``synthetic``; it must never be
presented as observed behaviour of real files.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CATEGORIES = ["document", "data", "image", "video", "archive", "other"]
_BASE_RATE = {"document": 0.25, "data": 0.35, "image": 0.12, "video": 0.06, "archive": 0.02, "other": 0.08}
_SIZE_MU = {"document": 12.0, "data": 13.0, "image": 13.5, "video": 18.0, "archive": 16.0, "other": 11.0}


@dataclass
class SyntheticDataset:
    files: pd.DataFrame  # file_id, category, size_bytes, created_day
    events: pd.DataFrame  # file_id, day (integer day index, may repeat)
    history_days: int
    seed: int
    kind: str = "synthetic"


def generate(n_files: int = 1500, history_days: int = 540, seed: int = 42) -> SyntheticDataset:
    rng = np.random.default_rng(seed)
    categories = rng.choice(CATEGORIES, size=n_files, p=[0.35, 0.15, 0.25, 0.08, 0.07, 0.10])
    created = rng.integers(0, history_days - 60, size=n_files)
    files = pd.DataFrame(
        {
            "file_id": np.arange(n_files),
            "category": categories,
            "size_bytes": [int(rng.lognormal(_SIZE_MU[c], 1.2)) + 1 for c in categories],
            "created_day": created,
        }
    )
    days = np.arange(history_days)
    event_files, event_days = [], []
    for fid, cat, c0 in zip(files.file_id, categories, created, strict=True):
        base = _BASE_RATE[cat] * rng.lognormal(0, 1.0)
        half_life = rng.choice([7, 21, 60, 180, 720], p=[0.25, 0.3, 0.2, 0.15, 0.1])
        age = days - c0
        intensity = np.where(age >= 0, base * 3 * 0.5 ** (np.maximum(age, 0) / half_life), 0.0)
        if rng.random() < 0.12:  # periodic: re-read around the start of each 30-day cycle
            intensity += np.where((age >= 0) & (days % 30 < 2), base * 2, 0.0)
        if rng.random() < 0.08:  # revival burst at a random later point
            start = rng.integers(c0, history_days)
            intensity += np.where((days >= start) & (days < start + rng.integers(5, 40)), base * 4, 0.0)
        intensity += np.where(age >= 0, 0.002, 0.0)  # background noise
        counts = rng.poisson(intensity)
        nz = np.nonzero(counts)[0]
        event_files.extend([fid] * int(counts[nz].sum()))
        event_days.extend(np.repeat(nz, counts[nz]).tolist())
    events = pd.DataFrame({"file_id": event_files, "day": event_days})
    return SyntheticDataset(files=files, events=events, history_days=history_days, seed=seed)
