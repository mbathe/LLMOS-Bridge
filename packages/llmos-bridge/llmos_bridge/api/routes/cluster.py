"""Cluster layer — Cluster information and node management endpoints.

Endpoints:
    GET  /cluster           — Cluster information
    GET  /cluster/health    — Cluster-wide health overview
    GET  /nodes             — List registered nodes
    GET  /nodes/{node_id}   — Node detail
    POST /nodes             — Register a remote node (dynamic)
    DELETE /nodes/{node_id} — Unregister a node
    POST /nodes/{node_id}/heartbeat — Trigger heartbeat on a node
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from llmos_bridge.api.dependencies import (
    ConfigDep,
    DiscoveryDep,
    HealthMonitorDep,
    IdentityStoreDep,
    LoadTrackerDep,
    NodeRegistryDep,
    QuarantineDep,
)
from llmos_bridge.api.schemas import (
    ClusterHealthResponse,
    ClusterResponse,
    NodeRegisterRequest,
    NodeResponse,
)

router = APIRouter(tags=["cluster"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_to_response(
    node_id: str,
    node: object,
    config: object,
    is_local: bool,
    load_tracker: object | None = None,
    quarantine: object | None = None,
) -> NodeResponse:
    """Build a NodeResponse from a BaseNode instance."""
    active_actions = 0
    if load_tracker is not None and hasattr(load_tracker, "get"):
        active_actions = load_tracker.get(node_id)
    is_quarantined = False
    if quarantine is not None and hasattr(quarantine, "is_quarantined"):
        is_quarantined = quarantine.is_quarantined(node_id)

    return NodeResponse(
        node_id=node_id,
        location=getattr(node, "_location", getattr(config.node, "location", "") if is_local else ""),
        available=node.is_available(),  # type: ignore[union-attr]
        is_local=is_local,
        modules=getattr(node, "_capabilities", []),
        last_heartbeat=getattr(node, "_last_heartbeat", None) or None,
        url=getattr(node, "_base_url", None),
        latency_ms=getattr(node, "_last_latency_ms", None),
        active_actions=active_actions,
        quarantined=is_quarantined,
    )


# ---------------------------------------------------------------------------
# Cluster endpoints
# ---------------------------------------------------------------------------


@router.get("/cluster", response_model=ClusterResponse, summary="Cluster information")
async def get_cluster_info(
    config: ConfigDep,
    store: IdentityStoreDep,
    node_registry: NodeRegistryDep,
) -> ClusterResponse:
    cluster_id = config.node.cluster_id or str(uuid.uuid5(uuid.NAMESPACE_DNS, config.node.cluster_name))
    app_count = 0
    if store is not None:
        apps = await store.list_applications()
        app_count = len(apps)
    return ClusterResponse(
        cluster_id=cluster_id,
        cluster_name=config.node.cluster_name,
        node_id=config.node.node_id,
        mode=config.node.mode,
        app_count=app_count,
        identity_enabled=config.identity.enabled,
    )


@router.get("/cluster/health", response_model=ClusterHealthResponse, summary="Cluster health overview")
async def get_cluster_health(
    config: ConfigDep,
    node_registry: NodeRegistryDep,
    load_tracker: LoadTrackerDep,
    quarantine: QuarantineDep,
) -> ClusterHealthResponse:
    if node_registry is None:
        # Standalone — just the local node.
        local = NodeResponse(
            node_id=config.node.node_id,
            location=config.node.location,
            available=True,
            is_local=True,
        )
        return ClusterHealthResponse(
            total_nodes=1,
            available_nodes=1,
            unavailable_nodes=0,
            nodes=[local],
        )

    nodes: list[NodeResponse] = []
    for nid in node_registry.list_nodes():
        node = node_registry.resolve(nid)
        is_local = nid == "local" or nid == config.node.node_id
        nodes.append(_node_to_response(
            nid, node, config, is_local,
            load_tracker=load_tracker, quarantine=quarantine,
        ))

    available = sum(1 for n in nodes if n.available)
    return ClusterHealthResponse(
        total_nodes=len(nodes),
        available_nodes=available,
        unavailable_nodes=len(nodes) - available,
        nodes=nodes,
    )


# ---------------------------------------------------------------------------
# Node endpoints
# ---------------------------------------------------------------------------


@router.get("/nodes", response_model=list[NodeResponse], summary="List registered nodes")
async def list_nodes(
    config: ConfigDep,
    node_registry: NodeRegistryDep,
    load_tracker: LoadTrackerDep,
    quarantine: QuarantineDep,
) -> list[NodeResponse]:
    if node_registry is None:
        return [
            NodeResponse(
                node_id=config.node.node_id,
                location=config.node.location,
                available=True,
                is_local=True,
            )
        ]

    results = []
    for nid in node_registry.list_nodes():
        node = node_registry.resolve(nid)
        is_local = nid == "local" or nid == config.node.node_id
        results.append(_node_to_response(
            nid, node, config, is_local,
            load_tracker=load_tracker, quarantine=quarantine,
        ))
    return results


@router.get("/nodes/{node_id}", response_model=NodeResponse, summary="Node detail")
async def get_node(
    node_id: str,
    config: ConfigDep,
    node_registry: NodeRegistryDep,
    load_tracker: LoadTrackerDep,
    quarantine: QuarantineDep,
) -> NodeResponse:
    if node_registry is None:
        if node_id == config.node.node_id or node_id == "local":
            return NodeResponse(
                node_id=config.node.node_id,
                location=config.node.location,
                available=True,
                is_local=True,
            )
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    node = node_registry.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    is_local = node_id == "local" or node_id == config.node.node_id
    return _node_to_response(
        node_id, node, config, is_local,
        load_tracker=load_tracker, quarantine=quarantine,
    )


@router.post("/nodes", response_model=NodeResponse, status_code=201, summary="Register a remote node")
async def register_node(
    body: NodeRegisterRequest,
    config: ConfigDep,
    node_registry: NodeRegistryDep,
    discovery: DiscoveryDep,
) -> NodeResponse:
    if discovery is None:
        raise HTTPException(
            status_code=400,
            detail="Node registration requires non-standalone mode (set node.mode to 'orchestrator' or 'node').",
        )

    # Check for duplicate.
    if node_registry is not None and node_registry.get_node(body.node_id) is not None:
        raise HTTPException(status_code=409, detail=f"Node '{body.node_id}' is already registered")

    node = await discovery.register_node(
        node_id=body.node_id,
        url=body.url,
        api_token=body.api_token,
        location=body.location,
    )
    return NodeResponse(
        node_id=node.node_id,
        url=node._base_url,
        location=node._location,
        available=node.is_available(),
        is_local=False,
        modules=node._capabilities,
        last_heartbeat=node._last_heartbeat,
    )


@router.delete("/nodes/{node_id}", summary="Unregister a node")
async def unregister_node(
    node_id: str,
    discovery: DiscoveryDep,
) -> dict[str, str]:
    if discovery is None:
        raise HTTPException(
            status_code=400,
            detail="Node management requires non-standalone mode.",
        )

    if node_id == "local":
        raise HTTPException(status_code=400, detail="Cannot unregister the local node")

    from llmos_bridge.exceptions import NodeNotFoundError

    try:
        await discovery.unregister_node(node_id)
    except NodeNotFoundError:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    return {"detail": f"Node '{node_id}' unregistered"}


@router.post("/nodes/{node_id}/heartbeat", summary="Trigger heartbeat on a node")
async def trigger_heartbeat(
    node_id: str,
    node_registry: NodeRegistryDep,
    health_monitor: HealthMonitorDep,
) -> dict[str, object]:
    if node_registry is None:
        raise HTTPException(status_code=400, detail="Node management requires non-standalone mode.")

    node = node_registry.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")

    if health_monitor is not None:
        health = await health_monitor.check_node(node_id)
    else:
        # No health monitor — check manually if it's a remote node.
        from llmos_bridge.orchestration.remote_node import RemoteNode

        if isinstance(node, RemoteNode):
            health = await node.heartbeat()
        else:
            health = {"status": "ok", "node_id": node_id, "type": "local", "available": True}

    return {"node_id": node_id, "health": health}
