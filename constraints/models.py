"""
Shared data structures for the centralized constraint-validation service.

These wrap the existing CSPValidator violation shape without forcing legacy
callers to migrate. Final specialization preference is SC9; this model still
tolerates legacy warning-severity records (including historical HC_SPEC data).
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConstraintViolation:
    rule: str
    subject: str = ""
    detail: str = ""
    severity: Optional[str] = None
    type: Optional[str] = None
    extra: dict = field(default_factory=dict)

    _KNOWN_KEYS = ("rule", "subject", "detail", "severity", "type")

    @classmethod
    def from_dict(cls, d: dict) -> "ConstraintViolation":
        return cls(
            rule=d.get("rule", ""),
            subject=d.get("subject", ""),
            detail=d.get("detail", ""),
            severity=d.get("severity"),
            type=d.get("type"),
            extra={k: v for k, v in d.items() if k not in cls._KNOWN_KEYS},
        )

    def to_dict(self) -> dict:
        out = {"rule": self.rule, "subject": self.subject, "detail": self.detail}
        if self.severity is not None:
            out["severity"] = self.severity
        if self.type is not None:
            out["type"] = self.type
        out.update(self.extra)
        return out

    @property
    def is_warning(self) -> bool:
        # Kept generic for backward compatibility with stored/legacy warnings.
        return self.severity == "warning"


@dataclass
class ValidationResult:
    violations: list = field(default_factory=list)

    @property
    def hard_violations(self) -> list:
        return [v for v in self.violations if v.get("severity") != "warning"]

    @property
    def warnings(self) -> list:
        return [v for v in self.violations if v.get("severity") == "warning"]

    @property
    def has_violations(self) -> bool:
        return bool(self.hard_violations)

    def as_violation_objects(self) -> list:
        return [ConstraintViolation.from_dict(v) for v in self.violations]


@dataclass
class ScheduleContext:
    schedule: list
    faculty_map: dict
    skip_rules: Optional[set] = None
    existing_load: Optional[dict] = None
    rooms_by_id: Optional[dict] = None
