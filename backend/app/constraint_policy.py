from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from backend.app.models.entities import Constraint


ConstraintType = Literal["HARD", "SOFT"]


@dataclass(frozen=True)
class ConstraintDefinition:
    constraint_name: str
    constraint_type: ConstraintType
    configurable: bool
    default_weight: int | None = None
    penalty_key: str | None = None


SOFT_RULE_PREFERRED_TEACHER = "preferred_teacher"
SOFT_RULE_PRIORITY_TEACHER = "priority_teacher"
SOFT_RULE_EMERGENCY_STAFF = "emergency_staff"
SOFT_RULE_SAME_SUBJECT = "same_subject"
SOFT_RULE_SPREAD_GROUP_LESSONS = "spread_group_lessons"
SOFT_RULE_CONSECUTIVE_TEACHER = "consecutive_teacher"
SOFT_RULE_BALANCE_TEACHER_LOAD = "balance_teacher_load"
SOFT_RULE_OPTION_END = "option_end"
SOFT_RULE_ROOM_PREFERENCE = "room_preference"


CONSTRAINT_DEFINITIONS: dict[str, ConstraintDefinition] = {
    "teacher_double_booking": ConstraintDefinition("teacher_double_booking", "HARD", False),
    "room_double_booking": ConstraintDefinition("room_double_booking", "HARD", False),
    "group_double_booking": ConstraintDefinition("group_double_booking", "HARD", False),
    "teacher_availability": ConstraintDefinition("teacher_availability", "HARD", False),
    "teacher_load_limits": ConstraintDefinition("teacher_load_limits", "HARD", False),
    "teacher_qualification": ConstraintDefinition("teacher_qualification", "HARD", False),
    "room_availability": ConstraintDefinition("room_availability", "HARD", False),
    "room_suitability": ConstraintDefinition("room_suitability", "HARD", False),
    "fixed_event_blocking": ConstraintDefinition("fixed_event_blocking", "HARD", False),
    "option_block_simultaneity": ConstraintDefinition("option_block_simultaneity", "HARD", False),
    "option_block_resource_clash": ConstraintDefinition("option_block_resource_clash", "HARD", False),
    "lesson_count": ConstraintDefinition("lesson_count", "HARD", False),
    "preferred_teacher": ConstraintDefinition("preferred_teacher", "SOFT", True, 3, SOFT_RULE_PREFERRED_TEACHER),
    "priority_teacher": ConstraintDefinition("priority_teacher", "SOFT", True, 2, SOFT_RULE_PRIORITY_TEACHER),
    "emergency_staff": ConstraintDefinition("emergency_staff", "SOFT", True, 8, SOFT_RULE_EMERGENCY_STAFF),
    "same_subject": ConstraintDefinition("same_subject", "SOFT", True, 5, SOFT_RULE_SAME_SUBJECT),
    "spread_group_lessons": ConstraintDefinition("spread_group_lessons", "SOFT", True, 2, SOFT_RULE_SPREAD_GROUP_LESSONS),
    "consecutive_teacher": ConstraintDefinition("consecutive_teacher", "SOFT", True, 2, SOFT_RULE_CONSECUTIVE_TEACHER),
    "balance_teacher_load": ConstraintDefinition("balance_teacher_load", "SOFT", True, 1, SOFT_RULE_BALANCE_TEACHER_LOAD),
    "option_end": ConstraintDefinition("option_end", "SOFT", True, 3, SOFT_RULE_OPTION_END),
}

SOFT_RULE_DEFINITIONS: dict[str, ConstraintDefinition] = {
    definition.penalty_key: definition
    for definition in CONSTRAINT_DEFINITIONS.values()
    if definition.constraint_type == "SOFT" and definition.penalty_key is not None
}

INTERNAL_SOFT_DEFAULTS = {
    SOFT_RULE_ROOM_PREFERENCE: 1,
}


def effective_soft_weight(constraints: Mapping[str, Constraint], penalty_key: str) -> int:
    definition = SOFT_RULE_DEFINITIONS.get(penalty_key)
    if definition is None:
        return INTERNAL_SOFT_DEFAULTS[penalty_key]

    constraint = constraints.get(definition.constraint_name)
    if constraint is None:
        return definition.default_weight or 0
    if constraint.constraint_type != "SOFT" or constraint.weight < 0:
        return definition.default_weight or 0
    if not constraint.enabled:
        return 0
    return constraint.weight
