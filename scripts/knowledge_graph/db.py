"""
Database helpers for the knowledge graph.
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from .config import DB_PATH
from .models import Entity, EntityAlias, Relationship, EntityProfile, ConceptDefinition


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Context manager for database transactions."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate_schema(conn: sqlite3.Connection) -> None:
    """Add knowledge graph tables to the database."""
    cursor = conn.cursor()

    # Canonical entities table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL
        )
    ''')

    # Name variations pointing to canonical entity
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entity_alias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            source TEXT,
            FOREIGN KEY (entity_id) REFERENCES entity(id)
        )
    ''')

    # Relationships between entities
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS relationship (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entity_id INTEGER NOT NULL,
            target_entity_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            year INTEGER,
            evidence TEXT,
            FOREIGN KEY (source_entity_id) REFERENCES entity(id),
            FOREIGN KEY (target_entity_id) REFERENCES entity(id)
        )
    ''')

    # LLM-generated person profiles
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entity_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER UNIQUE NOT NULL,
            summary TEXT,
            expertise TEXT,
            years_active TEXT,
            key_contributions TEXT,
            contact_info TEXT,
            FOREIGN KEY (entity_id) REFERENCES entity(id)
        )
    ''')

    # Camp vocabulary definitions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS concept_definition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER UNIQUE NOT NULL,
            definition TEXT,
            examples TEXT,
            related_concepts TEXT,
            category TEXT,
            FOREIGN KEY (entity_id) REFERENCES entity(id)
        )
    ''')

    # Entity embeddings for semantic search
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS entity_embedding (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id INTEGER UNIQUE NOT NULL,
            embedding TEXT NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (entity_id) REFERENCES entity(id)
        )
    ''')

    # Indexes for fast lookups
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_entity_type ON entity(type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_entity_slug ON entity(slug)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alias_entity ON entity_alias(entity_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_alias_alias ON entity_alias(alias)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rel_source ON relationship(source_entity_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rel_target ON relationship(target_entity_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rel_type ON relationship(type)')

    conn.commit()
    print("   Schema migration complete")


def normalize_slug(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    import re
    import unicodedata

    # Normalize unicode
    slug = unicodedata.normalize('NFKD', name)
    slug = slug.encode('ascii', 'ignore').decode('ascii')

    # Lowercase and replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', slug.lower())
    slug = slug.strip('-')

    return slug or 'unknown'


# Entity CRUD operations

def insert_entity(conn: sqlite3.Connection, entity: Entity) -> int:
    """Insert an entity and return its ID."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO entity (type, canonical_name, slug)
        VALUES (?, ?, ?)
    ''', (entity.type, entity.canonical_name, entity.slug))
    return cursor.lastrowid


def get_entity_by_slug(conn: sqlite3.Connection, slug: str) -> Optional[Entity]:
    """Get an entity by its slug."""
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entity WHERE slug = ?', (slug,))
    row = cursor.fetchone()
    if row:
        return Entity(
            id=row['id'],
            type=row['type'],
            canonical_name=row['canonical_name'],
            slug=row['slug']
        )
    return None


def get_entity_by_alias(conn: sqlite3.Connection, alias: str) -> Optional[Entity]:
    """Get an entity by any of its aliases (case-insensitive)."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT e.* FROM entity e
        JOIN entity_alias ea ON e.id = ea.entity_id
        WHERE LOWER(ea.alias) = LOWER(?)
    ''', (alias,))
    row = cursor.fetchone()
    if row:
        return Entity(
            id=row['id'],
            type=row['type'],
            canonical_name=row['canonical_name'],
            slug=row['slug']
        )
    return None


def insert_alias(conn: sqlite3.Connection, alias: EntityAlias) -> int:
    """Insert an entity alias."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO entity_alias (entity_id, alias, source)
        VALUES (?, ?, ?)
    ''', (alias.entity_id, alias.alias, alias.source))
    return cursor.lastrowid


def get_aliases_for_entity(conn: sqlite3.Connection, entity_id: int) -> list[str]:
    """Get all aliases for an entity."""
    cursor = conn.cursor()
    cursor.execute('SELECT alias FROM entity_alias WHERE entity_id = ?', (entity_id,))
    return [row['alias'] for row in cursor.fetchall()]


# Relationship operations

def insert_relationship(conn: sqlite3.Connection, rel: Relationship) -> int:
    """Insert a relationship."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO relationship (source_entity_id, target_entity_id, type, weight, year, evidence)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (rel.source_entity_id, rel.target_entity_id, rel.type, rel.weight, rel.year, rel.evidence_json()))
    return cursor.lastrowid


def get_relationships_for_entity(conn: sqlite3.Connection, entity_id: int) -> list[dict]:
    """Get all relationships where entity is source or target."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.*,
               e1.canonical_name as source_name,
               e2.canonical_name as target_name
        FROM relationship r
        JOIN entity e1 ON r.source_entity_id = e1.id
        JOIN entity e2 ON r.target_entity_id = e2.id
        WHERE r.source_entity_id = ? OR r.target_entity_id = ?
    ''', (entity_id, entity_id))
    return [dict(row) for row in cursor.fetchall()]


# Profile operations

def insert_profile(conn: sqlite3.Connection, profile: EntityProfile) -> int:
    """Insert or update an entity profile."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO entity_profile
        (entity_id, summary, expertise, years_active, key_contributions, contact_info)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        profile.entity_id,
        profile.summary,
        profile.expertise_json(),
        profile.years_active_json(),
        profile.key_contributions,
        profile.contact_info_json()
    ))
    return cursor.lastrowid


def get_profile(conn: sqlite3.Connection, entity_id: int) -> Optional[EntityProfile]:
    """Get profile for an entity."""
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM entity_profile WHERE entity_id = ?', (entity_id,))
    row = cursor.fetchone()
    if row:
        return EntityProfile(
            entity_id=row['entity_id'],
            summary=row['summary'] or "",
            expertise=json.loads(row['expertise'] or "[]"),
            years_active=json.loads(row['years_active'] or "[]"),
            key_contributions=row['key_contributions'] or "",
            contact_info=json.loads(row['contact_info'] or "{}")
        )
    return None


# Concept definition operations

def insert_concept_definition(conn: sqlite3.Connection, defn: ConceptDefinition) -> int:
    """Insert or update a concept definition."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO concept_definition
        (entity_id, definition, examples, related_concepts, category)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        defn.entity_id,
        defn.definition,
        defn.examples_json(),
        defn.related_concepts_json(),
        defn.category
    ))
    return cursor.lastrowid


def get_concept_definition(conn: sqlite3.Connection, entity_id: int) -> Optional[ConceptDefinition]:
    """Get definition for a concept entity."""
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM concept_definition WHERE entity_id = ?', (entity_id,))
    row = cursor.fetchone()
    if row:
        return ConceptDefinition(
            entity_id=row['entity_id'],
            definition=row['definition'] or "",
            examples=json.loads(row['examples'] or "[]"),
            related_concepts=json.loads(row['related_concepts'] or "[]"),
            category=row['category'] or ""
        )
    return None


# Utility queries

def get_all_people_with_context(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Get all people with their message counts and sample messages."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            p.id,
            p.name,
            p.email,
            p.phone,
            p.message_count,
            p.first_seen,
            p.last_seen,
            (SELECT GROUP_CONCAT(content, ' | ')
             FROM (SELECT content FROM person_content
                   WHERE person_id = p.id
                   ORDER BY timestamp DESC LIMIT ?)) as sample_messages
        FROM person p
        ORDER BY p.message_count DESC
    ''', (limit,))
    return [dict(row) for row in cursor.fetchall()]


def get_person_messages(conn: sqlite3.Connection, person_id: int, limit: int = 20) -> list[str]:
    """Get sample messages for a person."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT content FROM person_content
        WHERE person_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    ''', (person_id, limit))
    return [row['content'] for row in cursor.fetchall()]


def get_person_shifts(conn: sqlite3.Connection, person_id: int) -> list[dict]:
    """Get all shifts for a person."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT year, role, day, time_slot
        FROM shifts
        WHERE person_id = ?
        ORDER BY year DESC, day
    ''', (person_id,))
    return [dict(row) for row in cursor.fetchall()]


def get_person_roster(conn: sqlite3.Connection, person_id: int) -> list[dict]:
    """Get roster entries for a person."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT year, status, dues_paid, shelter_type, home_city
        FROM roster
        WHERE person_id = ?
        ORDER BY year DESC
    ''', (person_id,))
    return [dict(row) for row in cursor.fetchall()]


def get_camp_knowledge_sample(conn: sqlite3.Connection, category: str = None, limit: int = 50) -> list[dict]:
    """Get sample camp knowledge documents."""
    cursor = conn.cursor()
    if category:
        cursor.execute('''
            SELECT id, title, content, source_file, category
            FROM camp_knowledge
            WHERE category = ?
            LIMIT ?
        ''', (category, limit))
    else:
        cursor.execute('''
            SELECT id, title, content, source_file, category
            FROM camp_knowledge
            LIMIT ?
        ''', (limit,))
    return [dict(row) for row in cursor.fetchall()]
