import hashlib
import re
import time
from functools import lru_cache

import numpy as np

from .config import settings

DIMENSION = 384
GROUPS = {
    "ai": "artificial intelligence ml machine learning neural llm deep learning",
    "frontend": "frontend front-end ui user interface react",
    "backend": "backend back-end server api microservice",
    "security": "security cybersecurity infosec defensive",
    "data": "data etl pipelines warehouse analytics",
    "entry": "intern internships internship student trainee junior",
}


def hash_embedding(text: str) -> list[float]:
    tokens = re.findall(r"[\w+#]+", text.casefold())[:3500]
    vector = np.zeros(DIMENSION)
    for token in tokens:
        key = hashlib.blake2s(token.encode(), digest_size=4).digest()
        vector[int.from_bytes(key[:2], "little") % DIMENSION] += 1 if key[2] < 128 else -1
    normalized = " ".join(tokens)
    for i, (group, aliases) in enumerate(GROUPS.items()):
        if (
            group in tokens
            or any(alias in tokens for alias in aliases.split())
            or (group == "ai" and "machine learning" in normalized)
        ):
            vector[i] += 5
    if not np.any(vector):
        vector[-1] = 1
    return (vector / np.linalg.norm(vector)).tolist()


@lru_cache(maxsize=2)
def sentence_model(name):
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(name, local_files_only=True)
    except OSError:
        model = SentenceTransformer(name)
    if model.get_embedding_dimension() != DIMENSION:
        raise ValueError(f"Embedding model must produce {DIMENSION} dimensions")
    return model


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
GEMINI_BATCH = 100


def gemini_embed(name, texts, *, query):
    import httpx

    key = settings().gemini_api_key
    if key is None:
        raise ValueError("Set CAREERLENS_GEMINI_API_KEY to use Gemini embeddings")
    task = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
    vectors = []
    with httpx.Client(timeout=30) as client:
        for start in range(0, len(texts), GEMINI_BATCH):
            body = {
                "requests": [
                    {
                        "model": f"models/{name}",
                        "content": {"parts": [{"text": text[:8000] or " "}]},
                        "taskType": task,
                        "outputDimensionality": DIMENSION,
                    }
                    for text in texts[start : start + GEMINI_BATCH]
                ]
            }
            for attempt in range(6):
                response = client.post(
                    GEMINI_URL.format(model=name),
                    json=body,
                    headers={"x-goog-api-key": key.get_secret_value()},
                )
                if response.status_code not in (429, 500, 503) or attempt == 5:
                    break
                try:
                    wait = float(response.headers.get("retry-after", ""))
                except ValueError:
                    wait = 0
                time.sleep(min(max(wait, 5 * 2**attempt), 90))
            response.raise_for_status()
            for item in response.json()["embeddings"]:
                vector = np.asarray(item["values"], dtype=float)
                if vector.shape != (DIMENSION,):
                    raise ValueError(f"Embedding model must produce {DIMENSION} dimensions")
                vectors.append((vector / np.linalg.norm(vector)).tolist())
    return vectors


def embed(texts: list[str], *, query=False) -> list[list[float]]:
    name = settings().embedding_model
    if name == "local-hash-v1":
        return [hash_embedding(text) for text in texts]
    if name.startswith("gemini-"):
        return gemini_embed(name, texts, query=query)
    model = sentence_model(name)
    encode = model.encode_query if query else model.encode_document
    return encode(texts, normalize_embeddings=True).tolist()


@lru_cache(maxsize=1)
def cross_encoder(name):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name)


def profile_vector(profile):
    content = " ".join(
        [*profile.skills, *profile.tools, *profile.projects, *profile.domains, *profile.work_experience]
    )
    return embed([content])[0] if content.strip() else None
