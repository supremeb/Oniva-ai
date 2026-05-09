"""
Background workers for Oniva.

Workers process queued tasks asynchronously without blocking the main API.

Available Workers:
- classification_audit_worker: Drains classification audit events from Redis to PostgreSQL
"""
