"""Minimal relationship graph preview setup for Mem0 OSS."""

from mem0 import Memory


memory = Memory.from_config(
    {
        "vector_store": {
            "provider": "qdrant",
            "config": {"collection_name": "personal_memories"},
        },
        "relationship_graph": {
            "enabled": True,
            # URI, username, and password come from MEM0_GRAPH_NEO4J_*.
            "auto_start_worker": True,
            "reset_on_memory_reset": False,
            "graph_weight": 0.15,
        },
    }
)

try:
    memory.add("Alice works at Acme", user_id="alice")
    results = memory.search("Where does Alice work?", user_id="alice", explain=True)
    print(results)
    print(memory.relationship_graph.worker_service.health().model_dump(mode="json"))
finally:
    memory.close()
