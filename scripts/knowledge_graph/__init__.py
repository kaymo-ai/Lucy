"""
Knowledge Graph Pipeline for Snails Native

This module extracts entities (people, concepts, events, roles, equipment) from
the PS knowledge base, deduplicates them, builds a relationship graph, and
generates LLM-powered profiles and definitions.

Pipeline passes:
1. extract_entities.py - Extract entities from all sources
2. deduplicate.py - Merge duplicate entities (~1,142 -> ~400-500)
3. extract_relations.py - Build relationship graph
4. generate_profiles.py - LLM-generated person profiles and concept definitions
5. embed_entities.py - Generate embeddings for entity search

Usage:
    python -m knowledge_graph.run_pipeline
"""

__version__ = "0.1.0"
