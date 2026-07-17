"""Build content-free reconciliation evidence for full-capture gates."""

from __future__ import annotations

from collections.abc import Sequence
from hashlib import sha256


def _digest(values: Sequence[str], *, ordered: bool) -> str:
    normalized = list(values) if ordered else sorted(set(values))
    framed = "".join(f"{len(value)}:{value}" for value in normalized)
    return sha256(framed.encode("utf-8")).hexdigest()


def _duplicates(values: Sequence[str]) -> int:
    return len(values) - len(set(values))


def build_reconciliation_evidence(
    *,
    raw_message_ids: Sequence[str],
    markdown_message_ids: Sequence[str],
    ledger_message_ids: Sequence[str],
    discovered_attachment_ids: Sequence[str],
    saved_attachment_ids: Sequence[str],
    manifest_attachment_ids: Sequence[str],
    attachment_inventory_traversal_complete: bool,
) -> dict[str, object]:
    """Compare projections using identifiers while returning no identifiers/content."""
    message_sources = {
        "raw": list(raw_message_ids),
        "markdown": list(markdown_message_ids),
        "ledger": list(ledger_message_ids),
    }
    message_sets = {name: set(values) for name, values in message_sources.items()}
    ordered_digests = {
        name: _digest(values, ordered=True) for name, values in message_sources.items()
    }

    attachment_sources = {
        "discovered": list(discovered_attachment_ids),
        "saved": list(saved_attachment_ids),
        "manifest": list(manifest_attachment_ids),
    }
    attachment_sets = {
        name: set(values) for name, values in attachment_sources.items()
    }
    message_id_sets_equal = len({frozenset(value) for value in message_sets.values()}) == 1
    attachment_id_sets_equal = (
        len({frozenset(value) for value in attachment_sets.values()}) == 1
    )
    return {
        "message_count": len(message_sources["raw"]),
        "raw_record_count": len(message_sources["raw"]),
        "raw_message_count": len(message_sources["raw"]),
        "markdown_message_count": len(message_sources["markdown"]),
        "ledger_message_count": len(message_sources["ledger"]),
        "message_id_sets_equal": message_id_sets_equal,
        "ordered_message_digest_equal": len(set(ordered_digests.values())) == 1,
        "duplicate_message_count": max(
            _duplicates(values) for values in message_sources.values()
        ),
        "attachment_discovered_count": len(attachment_sources["discovered"]),
        "attachment_saved_count": len(attachment_sources["saved"]),
        "attachment_manifest_count": len(attachment_sources["manifest"]),
        "attachment_inventory_complete": bool(attachment_inventory_traversal_complete),
        "attachment_id_sets_equal": attachment_id_sets_equal,
        "evidence_producer": "discord_context_bridge.capture.reconcile.v1",
    }
