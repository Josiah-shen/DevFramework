"""Six-dimension scoring rubric for harness-creator audit.

Pure computation: no IO. Callers feed probe results, rubric returns scores.
"""

from dataclasses import dataclass, field
from typing import Callable


DIMENSIONS = ("doc", "lint", "build", "layer", "agent", "harness")
MAX_PER_DIM = 20
MAX_TOTAL = MAX_PER_DIM * len(DIMENSIONS)


@dataclass
class DimensionScore:
    name: str
    score: int
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    scores: list[DimensionScore]

    @property
    def total(self) -> int:
        return sum(d.score for d in self.scores)

    @property
    def normalized(self) -> int:
        return round(self.total / MAX_TOTAL * 100)

    @property
    def grade(self) -> str:
        n = self.normalized
        if n <= 20:
            return "bare"
        if n <= 70:
            return "gapped"
        return "healthy"

    @property
    def gaps(self) -> list[str]:
        collected: list[str] = []
        for dim in self.scores:
            collected.extend(dim.gaps)
        return collected


def score_three_tier(missing_count: int, has_soft_defect: bool) -> int:
    """Three-tier scoring primitive: 0 / 10 / 20.

    - missing_count >= 2: dimension is broken (0)
    - has_soft_defect: dimension present but something doesn't hold (10)
    - otherwise: healthy (20)
    """
    if missing_count >= 2:
        return 0
    if missing_count >= 1 or has_soft_defect:
        return 10
    return 20


Probe = Callable[[], DimensionScore]


def run_audit(probes: dict[str, Probe]) -> AuditResult:
    """Execute each dimension probe and collect scores.

    Probes must cover every name in DIMENSIONS. Missing probes raise
    KeyError up-front so misconfiguration fails loud.
    """
    scores: list[DimensionScore] = []
    for name in DIMENSIONS:
        probe = probes[name]
        result = probe()
        if result.name != name:
            raise ValueError(f"probe for '{name}' returned result named '{result.name}'")
        if not 0 <= result.score <= MAX_PER_DIM:
            raise ValueError(f"dimension '{name}' score {result.score} out of range")
        scores.append(result)
    return AuditResult(scores=scores)
