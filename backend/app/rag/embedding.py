"""
Embedding using sentence-transformers with bge-small-zh-v1.5.
"""
import logging
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)
_model: Optional[SentenceTransformer] = None

def get_embedding_model(model_name: str = "BAAI/bge-small-zh-v1.5"):
    global _model
    if _model is None:
        logger.info(f"Loading embedding model: {model_name}")
        _model = SentenceTransformer(model_name, trust_remote_code=True)
    return _model

def embed_texts(texts: List[str], model_name: str = "BAAI/bge-small-zh-v1.5") -> np.ndarray:
    model = get_embedding_model(model_name)
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

def embed_query(text: str, model_name: str = "BAAI/bge-small-zh-v1.5") -> np.ndarray:
    return embed_texts([text], model_name)[0]
