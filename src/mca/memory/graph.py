"""Knowledge graph store — PostgreSQL graph tables with CTE traversal.

Operates on mca.graph_nodes and mca.graph_edges tables.
Uses recursive CTEs for multi-hop graph traversal.
"""
from __future__ import annotations

import json
from typing import Any

from mca.log import get_logger

log = get_logger("graph")


class GraphStore:
    """PostgreSQL-backed knowledge graph store."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def build_graph(self, workspace: str, data: Any) -> dict[str, int]:
        """Insert extracted graph data into PostgreSQL.

        Strategy: DELETE existing data for this workspace, then INSERT fresh.

        Returns:
            {"nodes": count, "edges": count}
        """
        # Clear existing graph for this workspace
        self.conn.execute(
            "DELETE FROM mca.graph_edges WHERE source_id IN "
            "(SELECT id FROM mca.graph_nodes WHERE workspace = %s)",
            (workspace,),
        )
        self.conn.execute(
            "DELETE FROM mca.graph_nodes WHERE workspace = %s",
            (workspace,),
        )

        # Deduplicate nodes
        seen: set[tuple[str, str, str]] = set()
        unique_nodes = []
        for node in data.nodes:
            key = (node.node_type, node.name, node.file_path or "")
            if key not in seen:
                seen.add(key)
                unique_nodes.append(node)

        # Insert nodes, build key→id map
        node_map: dict[tuple[str, str, str], str] = {}
        for node in unique_nodes:
            row = self.conn.execute(
                """\
                INSERT INTO mca.graph_nodes (workspace, node_type, name, file_path, line_number, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (workspace, node.node_type, node.name, node.file_path,
                 node.line_number, json.dumps(node.metadata)),
            ).fetchone()
            key = (node.node_type, node.name, node.file_path or "")
            node_map[key] = row[0]

        # Insert edges
        edge_count = 0
        seen_edges: set[tuple[str, str, str]] = set()

        for edge in data.edges:
            src_key = (edge.source.node_type, edge.source.name, edge.source.file_path or "")
            tgt_key = (edge.target.node_type, edge.target.name, edge.target.file_path or "")

            src_id = node_map.get(src_key)
            tgt_id = node_map.get(tgt_key)
            if not src_id or not tgt_id or src_id == tgt_id:
                continue

            edge_key = (src_id, tgt_id, edge.edge_type)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            try:
                self.conn.execute(
                    """\
                    INSERT INTO mca.graph_edges (source_id, target_id, edge_type, weight, metadata)
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s)
                    """,
                    (src_id, tgt_id, edge.edge_type, edge.weight,
                     json.dumps(edge.metadata)),
                )
                edge_count += 1
            except Exception as e:
                log.debug("Edge insert failed: %s", e)

        log.info("Built graph for %s: %d nodes, %d edges",
                 workspace, len(unique_nodes), edge_count)
        return {"nodes": len(unique_nodes), "edges": edge_count}

    def query_node(self, workspace: str, name: str) -> list[dict[str, Any]]:
        """Find nodes by name (case-insensitive partial match)."""
        rows = self.conn.execute(
            """\
            SELECT id::text, node_type, name, file_path, line_number, metadata
            FROM mca.graph_nodes
            WHERE workspace = %s AND name ILIKE %s
            ORDER BY node_type, name
            """,
            (workspace, f"%{name}%"),
        ).fetchall()
        return [_node_row(r) for r in rows]

    def get_neighbors(
        self,
        node_id: str,
        edge_types: list[str] | None = None,
        direction: str = "both",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get neighbor nodes connected by edges."""
        results = []

        if direction in ("outgoing", "both"):
            sql = """\
                SELECT n.id::text, n.node_type, n.name, n.file_path, n.line_number,
                       n.metadata, e.edge_type, 'outgoing' AS direction
                FROM mca.graph_edges e
                JOIN mca.graph_nodes n ON n.id = e.target_id
                WHERE e.source_id = %s::uuid
            """
            params: list[Any] = [node_id]
            if edge_types:
                sql += " AND e.edge_type = ANY(%s)"
                params.append(edge_types)
            sql += " LIMIT %s"
            params.append(limit)
            results.extend(self.conn.execute(sql, params).fetchall())

        if direction in ("incoming", "both"):
            sql = """\
                SELECT n.id::text, n.node_type, n.name, n.file_path, n.line_number,
                       n.metadata, e.edge_type, 'incoming' AS direction
                FROM mca.graph_edges e
                JOIN mca.graph_nodes n ON n.id = e.source_id
                WHERE e.target_id = %s::uuid
            """
            params = [node_id]
            if edge_types:
                sql += " AND e.edge_type = ANY(%s)"
                params.append(edge_types)
            sql += " LIMIT %s"
            params.append(limit)
            results.extend(self.conn.execute(sql, params).fetchall())

        return [
            {**_node_row(r), "edge_type": r[6], "direction": r[7]}
            for r in results
        ]

    def find_by_name(
        self,
        workspace: str,
        name: str,
        node_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find nodes by exact or fuzzy name match."""
        sql = """\
            SELECT id::text, node_type, name, file_path, line_number, metadata
            FROM mca.graph_nodes
            WHERE workspace = %s AND name ILIKE %s
        """
        params: list[Any] = [workspace, f"%{name}%"]
        if node_type:
            sql += " AND node_type = %s"
            params.append(node_type)
        sql += " ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END, name LIMIT 20"
        params.append(name)
        rows = self.conn.execute(sql, params).fetchall()
        return [_node_row(r) for r in rows]

    def traverse(
        self,
        node_id: str,
        max_depth: int = 2,
        edge_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Recursive CTE traversal from a starting node.

        Returns all reachable nodes within max_depth hops.
        """
        edge_filter = ""
        params: list[Any] = [node_id]
        if edge_types:
            edge_filter = "AND e.edge_type = ANY(%s)"
            params.append(edge_types)
        params.append(max_depth)

        sql = f"""\
            WITH RECURSIVE reachable AS (
                SELECT id, node_type, name, file_path, line_number, metadata,
                       0 AS depth, ARRAY[id] AS visited
                FROM mca.graph_nodes
                WHERE id = %s::uuid

                UNION ALL

                SELECT n.id, n.node_type, n.name, n.file_path, n.line_number,
                       n.metadata, r.depth + 1, r.visited || n.id
                FROM reachable r
                JOIN mca.graph_edges e ON (e.source_id = r.id OR e.target_id = r.id)
                    {edge_filter}
                JOIN mca.graph_nodes n ON n.id = CASE
                    WHEN e.source_id = r.id THEN e.target_id
                    ELSE e.source_id
                END
                WHERE r.depth < %s
                  AND n.id != ALL(r.visited)
            )
            SELECT DISTINCT id::text, node_type, name, file_path, line_number, metadata
            FROM reachable
            ORDER BY name
        """
        rows = self.conn.execute(sql, params).fetchall()
        return [_node_row(r) for r in rows]

    def get_stats(self, workspace: str) -> dict[str, Any]:
        """Graph summary statistics for a workspace."""
        node_counts = self.conn.execute(
            """\
            SELECT node_type, COUNT(*)
            FROM mca.graph_nodes
            WHERE workspace = %s
            GROUP BY node_type
            ORDER BY COUNT(*) DESC
            """,
            (workspace,),
        ).fetchall()

        edge_counts = self.conn.execute(
            """\
            SELECT e.edge_type, COUNT(*)
            FROM mca.graph_edges e
            JOIN mca.graph_nodes n ON n.id = e.source_id
            WHERE n.workspace = %s
            GROUP BY e.edge_type
            ORDER BY COUNT(*) DESC
            """,
            (workspace,),
        ).fetchall()

        total_nodes = sum(r[1] for r in node_counts)
        total_edges = sum(r[1] for r in edge_counts)

        return {
            "workspace": workspace,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": {r[0]: r[1] for r in node_counts},
            "edges_by_type": {r[0]: r[1] for r in edge_counts},
        }


def _node_row(row) -> dict[str, Any]:
    """Convert a node query row to a dict."""
    meta = row[5]
    if isinstance(meta, str):
        meta = json.loads(meta)
    elif meta is None:
        meta = {}
    return {
        "id": row[0],
        "node_type": row[1],
        "name": row[2],
        "file_path": row[3],
        "line_number": row[4],
        "metadata": meta,
    }
