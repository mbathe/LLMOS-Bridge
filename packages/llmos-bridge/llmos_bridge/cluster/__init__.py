"""Cluster layer — cross-node communication and synchronisation.

Provides:
  - EventRebroadcaster : reads from Redis Streams and forwards to local bus
  - PermissionProxy    : HTTP proxy for checking permissions on the orchestrator
"""
