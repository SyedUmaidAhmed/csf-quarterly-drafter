"""Reading the data folder into graph inputs.

The folder is the whole contract. Point DATA_DIR somewhere else and the
workflow runs against different evidence with no code change — which is what
makes it rerunnable rather than a demo of one particular quarter.
"""

from __future__ import annotations

from .config import Settings
from .evidence import load_evidence, parse_field_table
from .understand import Cache
from .graph.state import initial_state


def load_inputs(settings: Settings) -> dict:
    if not settings.objective_file.exists():
        raise FileNotFoundError(
            f"no objective record at {settings.objective_file}. "
            "The data folder needs objective.md, prior_update.md (optional) "
            "and an evidence/ folder of markdown."
        )

    objective = parse_field_table(settings.objective_file.read_text(encoding="utf-8"))

    prior_update: dict[str, str] = {}
    if settings.prior_update_file.exists():
        prior_update = parse_field_table(settings.prior_update_file.read_text(encoding="utf-8"))

    return initial_state(
        quarter=settings.quarter,
        objective=objective,
        prior_update=prior_update,
        docs=load_evidence(settings.evidence_dir, cache=Cache(settings.understanding_dir)),
    )
