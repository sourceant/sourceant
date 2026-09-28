from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
import re
from typing import Any

MAX_SOURCE = 100_000
MAX_SCENARIOS = 100


def _semantic(value):
    if isinstance(value, dict):
        return {
            key: _semantic(item)
            for key, item in value.items()
            if key not in {"id", "location"}
        }
    if isinstance(value, (list, tuple)):
        return [_semantic(item) for item in value]
    return value


def _digest(value) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def parse_scenarios(source: str) -> dict[str, Any]:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Write a Gherkin feature with at least one scenario")
    if len(source.encode()) > MAX_SOURCE:
        raise ValueError("Gherkin source must not exceed 100 KB")
    return deepcopy(_parse(source))


@lru_cache(maxsize=128)
def _parse(source: str) -> dict[str, Any]:
    from gherkin import Parser
    from gherkin.errors import CompositeParserException, ParserException

    try:
        document = Parser().parse(source)
    except (CompositeParserException, ParserException) as error:
        raise ValueError(str(error)) from error
    feature = document.get("feature")
    if not feature or not feature["name"].strip():
        raise ValueError("A named Feature is required")
    scenarios = []
    identifiers = set()

    def walk(children, background=(), rule="", tags=(), rule_description=""):
        shared = tuple(background)
        for child in children:
            if "background" in child:
                shared += tuple(child["background"]["steps"])
            elif "rule" in child:
                item = child["rule"]
                walk(
                    item["children"],
                    shared,
                    item["name"],
                    (*tags, *item["tags"]),
                    item["description"],
                )
            elif "scenario" in child:
                item = child["scenario"]
                if not item["name"].strip() or not item["steps"]:
                    raise ValueError("Each scenario needs a name and at least one step")
                explicit = [
                    tag["name"][4:]
                    for tag in item["tags"]
                    if tag["name"].startswith("@id:")
                ]
                if len(explicit) > 1:
                    raise ValueError("A scenario may have only one @id: tag")
                identifier = (
                    explicit[0] if explicit else _digest([rule, item["name"]])[:24]
                )
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
                    raise ValueError(
                        "Scenario ids use letters, digits, underscores and hyphens"
                    )
                if identifier in identifiers:
                    raise ValueError(
                        "Scenario names must be unique within a rule, or have distinct @id: tags"
                    )
                identifiers.add(identifier)
                inherited_tags = [
                    tag["name"] for tag in (*feature["tags"], *tags, *item["tags"])
                ]
                scenarios.append(
                    {
                        **item,
                        "id": identifier,
                        "rule": rule,
                        "tags": inherited_tags,
                        "background": list(shared),
                        "revision": _digest(
                            [
                                _semantic(item),
                                _semantic(shared),
                                inherited_tags,
                                rule,
                                rule_description,
                                feature["name"],
                                feature["language"],
                                feature["description"],
                            ]
                        ),
                    }
                )
                if len(scenarios) > MAX_SCENARIOS:
                    raise ValueError("A requirement may have at most 100 scenarios")

    walk(feature["children"])
    if not scenarios:
        raise ValueError("Add at least one scenario")
    return {
        "source": source,
        "revision": _digest(_semantic(feature)),
        "name": feature["name"],
        "language": feature["language"],
        "scenarios": scenarios,
    }


def scenario_properties(properties):
    if "behavior" not in properties:
        return properties
    behavior = properties["behavior"]
    if behavior is None:
        return {key: value for key, value in properties.items() if key != "behavior"}
    if not isinstance(behavior, dict):
        raise ValueError("Behavior must contain Gherkin source")
    return {**properties, "behavior": parse_scenarios(behavior.get("source"))}


def scenario_link_properties(requirement, link):
    identifier = link.properties.get("scenario_id")
    if not identifier:
        return link.properties
    scenarios = requirement.properties.get("behavior", {}).get("scenarios", [])
    scenario = next((item for item in scenarios if item["id"] == identifier), None)
    if scenario is None:
        raise ValueError("The scenario does not belong to this requirement")
    revision = link.properties.get("scenario_revision", scenario["revision"])
    if revision != scenario["revision"]:
        raise ValueError("The scenario changed; refresh it before linking evidence")
    return {**link.properties, "scenario_revision": revision}
