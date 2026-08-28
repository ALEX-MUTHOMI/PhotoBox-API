"""Shared helpers for DSA invariant tests."""
from __future__ import annotations

import json
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from gallery.models import Photo


def walk_plan_nodes(plan: dict):
    """Yield every node in an EXPLAIN (FORMAT JSON) plan tree."""
    stack = [plan]
    while stack:
        node = stack.pop()
        yield node
        for child in node.get("Plans") or []:
            stack.append(child)


def explain_queryset(queryset, *, disable_seqscan: bool = False) -> dict:
    """Run EXPLAIN (FORMAT JSON) for a queryset; return the root Plan dict."""
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        if disable_seqscan:
            # Keep bitmapscan enabled — GIN/trgm plans are Bitmap Index Scans.
            cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(f"EXPLAIN (FORMAT JSON) {sql}", params)
        payload = cursor.fetchone()[0]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload[0]["Plan"]


def plan_mentions_index(plan: dict, index_name: str) -> bool:
    for node in walk_plan_nodes(plan):
        if node.get("Index Name") == index_name:
            return True
        # Bitmap Index Scan may nest the name
        if index_name in json.dumps(node):
            if "Index" in node.get("Node Type", ""):
                return True
    return False


def plan_has_seq_scan_on(plan: dict, relation: str) -> bool:
    for node in walk_plan_nodes(plan):
        if node.get("Node Type") == "Seq Scan" and node.get("Relation Name") == relation:
            return True
    return False


def plan_has_offset(plan: dict) -> bool:
    for node in walk_plan_nodes(plan):
        if node.get("Offset") is not None and int(node.get("Offset") or 0) > 0:
            return True
        dump = json.dumps(node)
        if '"Offset"' in dump and node.get("Offset"):
            return True
    return False


def analyze_table(table: str = "gallery_photo") -> None:
    """Refresh planner stats after bulk seeds (required for honest EXPLAIN)."""
    with connection.cursor() as cursor:
        cursor.execute(f"ANALYZE {table}")


def plan_has_ordered_index_scan(plan: dict, index_name: str) -> bool:
    for node in walk_plan_nodes(plan):
        if node.get("Index Name") == index_name and node.get("Node Type") in {
            "Index Scan",
            "Index Only Scan",
        }:
            return True
    return False


def seed_scene_photos(scene, count: int, *, filename_prefix: str = "photo") -> None:
    """Bulk-create photos with distinct uploaded_at for keyset/EXPLAIN tests."""
    base = timezone.now()
    batch = [
        Photo(
            scene=scene,
            original_filename=f"{filename_prefix}_{i:05d}.jpg",
            file_size_bytes=1024,
            status="READY",
            media_type="IMAGE",
            r2_object_key=f"raw/seed/{scene.id}/{i:05d}.jpg",
            is_processed=True,
        )
        for i in range(count)
    ]
    Photo.objects.bulk_create(batch, batch_size=500)
    photos = list(Photo.objects.filter(scene=scene).order_by("id"))
    for i, photo in enumerate(photos):
        photo.uploaded_at = base - timedelta(seconds=i)
    Photo.objects.bulk_update(photos, ["uploaded_at"], batch_size=500)
    analyze_table("gallery_photo")
