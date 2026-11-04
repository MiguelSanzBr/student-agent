from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class EnrollmentRef:
    index: Literal["1", "2", "3"]
    enrollment_key: UUID
    label: str
    brand: str
    brand_family: str
    modality: Literal["presencial", "ead"]
    status: str
    is_primary: bool


@dataclass(frozen=True)
class StudentScope:
    person_key: UUID
    enrollments: tuple[EnrollmentRef, ...]
    brand_families: tuple[str, ...]
