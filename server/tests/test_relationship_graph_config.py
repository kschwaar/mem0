from relationship_graph_config import build_relationship_graph_config


def test_relationship_graph_is_disabled_without_neo4j_provider():
    assert build_relationship_graph_config("", {}) is None


def test_relationship_graph_uses_compose_neo4j_settings():
    config = build_relationship_graph_config(
        "neo4j",
        {
            "NEO4J_URL": "bolt://neo4j-mem0:7687",
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "local-password",
        },
    )

    assert config == {
        "enabled": True,
        "provider": "neo4j",
        "uri": "bolt://neo4j-mem0:7687",
        "username": "neo4j",
        "password": "local-password",
        "database": "neo4j",
        "query_timeout_seconds": 5.0,
    }


def test_relationship_graph_prefers_dedicated_connection_settings():
    config = build_relationship_graph_config(
        "neo4j",
        {
            "MEM0_GRAPH_NEO4J_URI": "neo4j://graph.example:7687",
            "MEM0_GRAPH_NEO4J_USERNAME": "graph-user",
            "MEM0_GRAPH_NEO4J_PASSWORD": "graph-password",
            "MEM0_GRAPH_NEO4J_DATABASE": "mem0",
            "MEM0_GRAPH_QUERY_TIMEOUT_SECONDS": "2.5",
            "NEO4J_URL": "bolt://legacy:7687",
            "NEO4J_USERNAME": "legacy-user",
            "NEO4J_PASSWORD": "legacy-password",
        },
    )

    assert config == {
        "enabled": True,
        "provider": "neo4j",
        "uri": "neo4j://graph.example:7687",
        "username": "graph-user",
        "password": "graph-password",
        "database": "mem0",
        "query_timeout_seconds": 2.5,
    }
