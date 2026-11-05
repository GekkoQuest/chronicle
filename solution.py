"""Evaluator entry point. No ingestion, filesystem reads or network calls on import."""
from chronicle.engine import KnowledgeSystem


def build() -> KnowledgeSystem:
    return KnowledgeSystem()
