"""
Data models for the knowledge graph.
"""

from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class Entity:
    """A canonical entity (person, concept, event, role, equipment)."""
    id: Optional[int] = None
    type: str = ""  # 'person', 'concept', 'event', 'role', 'equipment'
    canonical_name: str = ""
    slug: str = ""  # normalized identifier

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "canonical_name": self.canonical_name,
            "slug": self.slug
        }


@dataclass
class EntityAlias:
    """A name variation pointing to a canonical entity."""
    entity_id: int
    alias: str
    source: str = ""  # 'chat', 'roster', 'shifts', 'docs'


@dataclass
class Relationship:
    """A relationship between two entities."""
    source_entity_id: int
    target_entity_id: int
    type: str  # 'leads', 'works_on', 'knows_about', 'attended', 'mentioned_with'
    weight: float = 1.0
    year: Optional[int] = None
    evidence: list = field(default_factory=list)  # source quotes

    def evidence_json(self) -> str:
        return json.dumps(self.evidence)


@dataclass
class EntityProfile:
    """LLM-generated profile for a person entity."""
    entity_id: int
    summary: str = ""
    expertise: list = field(default_factory=list)
    years_active: list = field(default_factory=list)
    key_contributions: str = ""
    contact_info: dict = field(default_factory=dict)

    def expertise_json(self) -> str:
        return json.dumps(self.expertise)

    def years_active_json(self) -> str:
        return json.dumps(self.years_active)

    def contact_info_json(self) -> str:
        return json.dumps(self.contact_info)


@dataclass
class ConceptDefinition:
    """LLM-generated definition for a concept entity."""
    entity_id: int
    definition: str = ""
    examples: list = field(default_factory=list)
    related_concepts: list = field(default_factory=list)
    category: str = ""  # 'culture', 'equipment', 'location', 'event', 'role'

    def examples_json(self) -> str:
        return json.dumps(self.examples)

    def related_concepts_json(self) -> str:
        return json.dumps(self.related_concepts)


@dataclass
class ExtractedEntities:
    """Container for entities extracted from a document."""
    concepts: list = field(default_factory=list)
    events: list = field(default_factory=list)
    equipment: list = field(default_factory=list)
    roles: list = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict) -> "ExtractedEntities":
        return cls(
            concepts=data.get("concepts", []),
            events=data.get("events", []),
            equipment=data.get("equipment", []),
            roles=data.get("roles", [])
        )
