"""GET /context — System prompt and LLM context endpoint.

Returns a dynamically generated system prompt built from the current
daemon state: loaded modules, permission profile, IML v2 rules, and examples.

This is the primary integration point for LLM applications — the SDK calls
this endpoint once at startup to get the full system prompt.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from llmos_bridge import __version__
from llmos_bridge.api.dependencies import AuthDep, ConfigDep, RegistryDep, ScannerPipelineDep
from llmos_bridge.api.prompt import SystemPromptGenerator

router = APIRouter(tags=["context"])


@router.get("/context", summary="Get the LLM system prompt")
async def get_context(
    _auth: AuthDep,
    registry: RegistryDep,
    config: ConfigDep,
    scanner_pipeline: ScannerPipelineDep,
    include_schemas: bool = Query(
        default=True,
        description="Include full parameter schemas for each action.",
    ),
    include_examples: bool = Query(
        default=True,
        description="Include few-shot IML plan examples.",
    ),
    max_actions_per_module: int | None = Query(
        default=None,
        ge=1,
        le=100,
        description="Limit number of actions shown per module (None = all).",
    ),
    format: str = Query(
        default="full",
        description="Response format: 'full' (JSON with metadata) or 'prompt' (raw text).",
    ),
) -> Any:
    """Generate and return the LLM system prompt.

    The prompt is dynamically built from the currently loaded modules and
    the active permission profile. It includes IML v2 protocol rules,
    capability descriptions, parameter schemas, and examples.

    Use ``format=prompt`` to get raw text suitable for direct injection
    as a system message. Use ``format=full`` (default) to get a JSON
    response with metadata.
    """
    manifests = _collect_manifests(registry)
    context_snippets = _collect_context_snippets(registry)

    generator = SystemPromptGenerator(
        manifests=manifests,
        permission_profile=config.security.permission_profile,
        daemon_version=__version__,
        include_schemas=include_schemas,
        include_examples=include_examples,
        max_actions_per_module=max_actions_per_module,
        context_snippets=context_snippets,
        scanner_pipeline_active=scanner_pipeline is not None and scanner_pipeline.enabled,
    )

    if format == "prompt":
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(generator.generate(), media_type="text/plain")

    return generator.to_dict()


def _collect_manifests(registry: RegistryDep) -> list:
    """Collect ModuleManifest objects from all available modules."""
    from llmos_bridge.modules.manifest import ModuleManifest

    manifests: list[ModuleManifest] = []
    for module_id in registry.list_modules():
        if registry.is_available(module_id):
            try:
                manifests.append(registry.get_manifest(module_id))
            except Exception:
                pass
    return manifests


def _collect_context_snippets(registry: RegistryDep) -> dict[str, str]:
    """Collect dynamic context snippets from loaded modules."""
    snippets: dict[str, str] = {}
    for module_id in registry.list_available():
        try:
            module = registry.get(module_id)
            snippet = module.get_context_snippet()
            if snippet:
                snippets[module_id] = snippet
        except Exception:
            pass
    return snippets
