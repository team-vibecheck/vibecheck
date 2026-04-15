"""VibeCheck persistent sidecar for QA interaction.

This module intentionally uses lazy exports to avoid importing heavy
dependencies when submodules (e.g. ``qa.sidecar.server``) import package peers.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SidecarClient",
    "ensure_sidecar_running",
    "get_config",
    "shutdown_sidecar",
    "attach_lease",
    "heartbeat_lease",
    "detach_lease",
    "prune_stale_leases",
    "count_active_leases",
    "set_session_state",
    "get_presence_snapshot",
    "open_ui_once_for_pid",
]


def __getattr__(name: str) -> Any:
    if name == "SidecarClient":
        from qa.sidecar.client import SidecarClient

        return SidecarClient
    if name in {"ensure_sidecar_running", "get_config", "shutdown_sidecar"}:
        from qa.sidecar.lifecycle import ensure_sidecar_running, get_config, shutdown_sidecar

        return {
            "ensure_sidecar_running": ensure_sidecar_running,
            "get_config": get_config,
            "shutdown_sidecar": shutdown_sidecar,
        }[name]
    if name in {
        "attach_lease",
        "heartbeat_lease",
        "detach_lease",
        "prune_stale_leases",
        "count_active_leases",
    }:
        from qa.sidecar.leases import (
            attach_lease,
            count_active_leases,
            detach_lease,
            heartbeat_lease,
            prune_stale_leases,
        )

        return {
            "attach_lease": attach_lease,
            "heartbeat_lease": heartbeat_lease,
            "detach_lease": detach_lease,
            "prune_stale_leases": prune_stale_leases,
            "count_active_leases": count_active_leases,
        }[name]
    if name in {"set_session_state", "get_presence_snapshot"}:
        from qa.sidecar.presence import get_presence_snapshot, set_session_state

        return {
            "set_session_state": set_session_state,
            "get_presence_snapshot": get_presence_snapshot,
        }[name]
    if name == "open_ui_once_for_pid":
        from qa.sidecar.ui_open import open_ui_once_for_pid

        return open_ui_once_for_pid
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
