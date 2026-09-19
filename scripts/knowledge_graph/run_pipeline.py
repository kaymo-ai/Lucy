#!/usr/bin/env python3
"""
Knowledge Graph Pipeline Orchestrator

Runs all passes of the knowledge graph extraction pipeline:
1. Extract entities from all sources
2. Deduplicate person entities
3. Extract relationships
4. Generate profiles and definitions
5. Generate entity embeddings

Usage:
    python -m knowledge_graph.run_pipeline [options]

Options:
    --skip-llm          Skip all LLM-based processing (fast mode)
    --pass N            Run only pass N (1-5)
    --dry-run           Show what would be done without making changes
"""

import argparse
import sys
import subprocess
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent


def run_pass(name: str, module: str, args: list[str], dry_run: bool = False) -> bool:
    """Run a pipeline pass."""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    if dry_run:
        print(f"   Would run: python -m knowledge_graph.{module} {' '.join(args)}")
        return True

    cmd = [sys.executable, "-m", f"knowledge_graph.{module}"] + args

    result = subprocess.run(
        cmd,
        cwd=SCRIPTS_DIR.parent,  # Run from scripts directory
        capture_output=False
    )

    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(
        description='Run the knowledge graph extraction pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Passes:
  1. extract_entities  - Extract people, concepts, events from all sources
  2. deduplicate       - Merge duplicate person entities
  3. extract_relations - Build relationship graph
  4. generate_profiles - LLM-generated profiles and definitions
  5. embed_entities    - Generate embeddings for semantic search

Examples:
  # Run full pipeline
  python -m knowledge_graph.run_pipeline

  # Run without LLM calls (fast, uses existing data only)
  python -m knowledge_graph.run_pipeline --skip-llm

  # Run only entity extraction
  python -m knowledge_graph.run_pipeline --pass 1

  # Run only embedding generation
  python -m knowledge_graph.run_pipeline --pass 5
        """
    )
    parser.add_argument('--skip-llm', action='store_true',
                        help='Skip LLM-based processing')
    parser.add_argument('--pass', type=int, dest='only_pass', metavar='N',
                        help='Run only pass N (1-5)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be done')
    parser.add_argument('--limit', type=int, default=0,
                        help='Limit items processed per pass')
    args = parser.parse_args()

    print("=" * 60)
    print("  Knowledge Graph Pipeline")
    print("=" * 60)

    passes = [
        ("Pass 1: Entity Extraction", "extract_entities", ["--skip-llm"] if args.skip_llm else []),
        ("Pass 2: Deduplication", "deduplicate", ["--skip-llm"] if args.skip_llm else []),
        ("Pass 3: Relationship Extraction", "extract_relations", ["--skip-llm"] if args.skip_llm else []),
        ("Pass 4: Profile Generation", "generate_profiles", []),
        ("Pass 5: Entity Embeddings", "embed_entities", []),
    ]

    # Skip profile generation if --skip-llm
    if args.skip_llm:
        passes = [p for p in passes if p[1] != "generate_profiles"]

    # Add limit argument if specified
    if args.limit > 0:
        passes = [
            (name, module, pass_args + ["--limit", str(args.limit)])
            for name, module, pass_args in passes
        ]

    # Run only specific pass if requested
    if args.only_pass:
        if 1 <= args.only_pass <= 5:
            passes = [passes[args.only_pass - 1]]
        else:
            print(f"Error: Invalid pass number {args.only_pass}. Must be 1-5.")
            sys.exit(1)

    # Run passes
    success = True
    for name, module, pass_args in passes:
        if not run_pass(name, module, pass_args, args.dry_run):
            print(f"\n   Error: {name} failed")
            success = False
            break

    # Summary
    print("\n" + "=" * 60)
    if success:
        print("  Pipeline completed successfully!")
    else:
        print("  Pipeline failed. Check errors above.")
    print("=" * 60)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
