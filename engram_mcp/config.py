import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "engram_memories"
VECTOR_SIZE = 768

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = "nomic-embed-text"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
EXTRACT_MODEL = "claude-haiku-4-5-20251001"

VECTOR_WEIGHT = float(os.getenv("VECTOR_WEIGHT", "0.6"))
GRAPH_WEIGHT = float(os.getenv("GRAPH_WEIGHT", "0.4"))
DEFAULT_RETRIEVE_LIMIT = int(os.getenv("DEFAULT_RETRIEVE_LIMIT", "10"))

CHUNK_SIZE = 512        # max chars per chunk
CHUNK_OVERLAP = 64      # overlap between chunks

MEMORY_TYPES = {"feedback", "user", "project", "reference", "decision", "error"}
