"""The scenario a job runs, and the ids it may name.

A scenario is the explorer's format (`scenarios[].model_details` in its
data/results.json), which is also what the RaMa-Scene front end sends: a list of
steps, each scaling the cells picked by product x originReg x consumedBy x
consumedReg by (1 + techChange / 100). consumedBy "Y: Final Consumption" edits
final demand; any other consumer edits the technical coefficients A.
"""

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

if TYPE_CHECKING:
    from pathlib import Path

# Ids are the engine's global ids (db.sqlite3), as the RaMa-Scene front end sends them.
Ids = Annotated[list[StrictInt], Field(min_length=1, max_length=300)]
# A cut deeper than -100% would make coefficients negative. The upper bound is
# only a guard against typos: +1000% already multiplies a cell by eleven.
MIN_CHANGE, MAX_CHANGE = -100.0, 1000.0


class Step(BaseModel):
    """One intervention: which cells, and by how much (percent)."""

    model_config = ConfigDict(extra="forbid")

    product: Ids
    originReg: Ids  # noqa: N815 - the explorer's and the engine's key
    consumedBy: Ids  # noqa: N815
    consumedReg: Ids  # noqa: N815
    # A one-element list, as the front end sends it; the engine reads techChange[0].
    techChange: Annotated[  # noqa: N815
        list[Annotated[float, Field(allow_inf_nan=False, ge=MIN_CHANGE, le=MAX_CHANGE)]],
        Field(min_length=1, max_length=1),
    ]

    @field_validator("techChange", mode="before")
    @classmethod
    def _no_strings(cls, value: object) -> object:
        # float("nan") and "1e400" would pass a lax float; the engine's own
        # float() is where RaMa-Scene let them through. Numbers only.
        if isinstance(value, list) and any(isinstance(v, (str, bool)) for v in value):
            msg = "techChange must hold a number, not a string or boolean"
            raise ValueError(msg)
        return value


class ScenarioRequest(BaseModel):
    """POST /jobs body."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="custom scenario", max_length=200)
    model_details: list[Step] = Field(min_length=1, max_length=10)
    # Also run the four Octave reference queries on the scenario's matrices. They
    # reproduce only when the scenario changes nothing (a 0% change).
    reference_check: bool = False


@dataclass(frozen=True)
class Catalog:
    """The ids a scenario may name, with their labels, from the engine database."""

    products: dict[int, str]
    regions: dict[int, str]
    consumers: dict[int, str]
    final_consumption: frozenset[int]

    @classmethod
    def from_db(cls, db: Path) -> Catalog:
        """Read the three id tables; `immutable` so nothing, not even WAL, is written."""
        with sqlite3.connect(f"file:{db}?immutable=1", uri=True) as con:

            def table(name: str) -> dict[int, str]:
                rows = con.execute(f"SELECT global_id, name FROM ramascene_{name} ORDER BY global_id")  # noqa: S608
                return dict(rows.fetchall())

            final = con.execute(
                "SELECT global_id FROM ramascene_modellingproduct WHERE identifier = 'FINALCONSUMPTION'"
            ).fetchall()
            catalog = cls(table("product"), table("country"), table("modellingproduct"), frozenset(r[0] for r in final))
        con.close()
        return catalog

    def problems(self, scenario: ScenarioRequest) -> list[str]:
        """Every unknown id, and any step mixing final demand with intermediate consumers."""
        found = []
        for n, step in enumerate(scenario.model_details):
            for key, known in (
                ("product", self.products),
                ("originReg", self.regions),
                ("consumedBy", self.consumers),
                ("consumedReg", self.regions),
            ):
                unknown = sorted(set(getattr(step, key)) - set(known))
                if unknown:
                    found.append(f"model_details[{n}].{key}: unknown ids {unknown}")
            final = {c in self.final_consumption for c in step.consumedBy}
            if len(final) > 1:
                # The engine decides from consumedBy[0] alone which matrix to edit.
                found.append(f"model_details[{n}].consumedBy: mixes final consumption with intermediate consumers")
        return found

    def as_json(self) -> dict[str, object]:
        """For GET /catalog: what a client can offer in its pickers."""

        def listing(ids: dict[int, str]) -> list[dict[str, object]]:
            return [{"id": i, "name": name} for i, name in ids.items()]

        return {
            "products": listing(self.products),
            "regions": listing(self.regions),
            "consumers": listing(self.consumers),
            "final_consumption": sorted(self.final_consumption),
            "tech_change_percent": {"min": MIN_CHANGE, "max": MAX_CHANGE},
        }
