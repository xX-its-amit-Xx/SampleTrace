"""Parsers that turn raw NGS artifacts into ``CanonicalSample`` lists.

Each parser is independent and returns ``list[CanonicalSample]`` so the
reconciler can treat them uniformly regardless of source.

Implementations are added in commit 2.
"""
