"""
Configuration for the knowledge graph pipeline.
"""

import os
from pathlib import Path

# Paths
SCRIPTS_DIR = Path(__file__).parent.parent
OUTPUT_DIR = SCRIPTS_DIR / "output"
DB_PATH = OUTPUT_DIR / "ps_knowledge.db"

# Claude API configuration
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Model selection - Haiku for extraction, Sonnet for profiles
EXTRACTION_MODEL = "claude-haiku-4-20250514"    # Fast, cheap for extraction
PROFILE_MODEL = "claude-sonnet-4-20250514"      # Better quality for profiles

# Embedding model (same as existing pipeline)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Processing settings
BATCH_SIZE = 50
MAX_RETRIES = 3
RATE_LIMIT_DELAY = 0.5  # seconds between API calls

# Entity types
ENTITY_TYPES = ["person", "concept", "event", "role", "equipment"]

# LLM Prompts
ENTITY_EXTRACTION_PROMPT = """Extract camp-specific entities from this document. Return JSON with these arrays:

- concepts: Camp terminology (e.g., MOOP, WAP, playa, theme camp)
- events: Named events (e.g., pig roast, sunrise set, Karaoke night)
- equipment: Camp gear (e.g., generator, shade structure, art car)
- roles: Camp roles (e.g., shift lead, placement liaison, kitchen lead)

Only include items that appear in the text. Be specific and include context.

Document:
{content}

Return ONLY valid JSON, no explanation:
{{"concepts": [...], "events": [...], "equipment": [...], "roles": [...]}}"""

DEDUP_VERIFICATION_PROMPT = """Are these two names referring to the same person? Consider:
- Nicknames, variations, and typos
- Context from their messages

Name 1: "{name1}"
Context 1: {context1}

Name 2: "{name2}"
Context 2: {context2}

Return ONLY "yes" or "no"."""

PERSON_PROFILE_PROMPT = """Generate a profile for this Burning Man camp member. Be concise and factual.

Name: {name}
Messages sample:
{messages}

Shifts/Roles:
{shifts}

Roster info:
{roster}

Return JSON with:
{{
  "summary": "One sentence describing who they are and their role in camp",
  "expertise": ["list", "of", "areas"],
  "years_active": [2023, 2024],
  "key_contributions": "Brief description of notable contributions"
}}

Return ONLY valid JSON:"""

CONCEPT_DEFINITION_PROMPT = """Define this Burning Man/camp term based on how it's used:

Term: {term}
Example usages:
{examples}

Return JSON with:
{{
  "definition": "Clear, concise definition",
  "category": "culture|equipment|location|event|role",
  "related_terms": ["list", "of", "related"]
}}

Return ONLY valid JSON:"""
