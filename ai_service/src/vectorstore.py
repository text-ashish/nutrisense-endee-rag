import json
import os
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests

try:
    import msgpack
except Exception:  # pragma: no cover - dependency is declared in requirements
    msgpack = None

VECTOR_DB_BACKEND = os.getenv("VECTOR_DB_BACKEND", "endee").strip().lower()


def parse_time(value: Any) -> str:
    """Convert '10 min', '1 hr 20 min', or numeric values to integer minutes as a string."""
    if pd.isna(value):
        return ""

    if isinstance(value, (int, float)):
        return str(int(value))

    if isinstance(value, str):
        text = value.lower().strip()
        total_minutes = 0

        if "hr" in text:
            try:
                hours = int("".join(ch for ch in text.split("hr")[0] if ch.isdigit()))
                total_minutes += hours * 60
                text = text.split("hr", 1)[1]
            except ValueError:
                pass

        if "min" in text:
            try:
                minutes = int("".join(ch for ch in text.split("min")[0] if ch.isdigit()))
                total_minutes += minutes
            except ValueError:
                pass

        return str(total_minutes) if total_minutes > 0 else ""

    return ""


def _value_or_default(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, float) and pd.isna(value):
        return default
    return value


def _to_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]

    text = str(value).strip()
    if not text:
        return []

    # Try JSON-like list first
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip().lower() for item in parsed if str(item).strip()]
        except Exception:
            pass

    return [part.strip().lower() for part in text.split(",") if part.strip()]


def _build_metadata(df: pd.DataFrame, idx: int) -> Dict[str, Any]:
    return {
        "recipe_name": str(_value_or_default(df.get("recipe_name", [""])[idx], "")),
        "ingredients": str(_value_or_default(df.get("ingredients", [""])[idx], "")),
        "directions": str(_value_or_default(df.get("directions", [""])[idx], "")),
        "prep_time": parse_time(df.get("prep_time", [""])[idx]),
        "cook_time": parse_time(df.get("cook_time", [""])[idx]),
        "total_time": parse_time(df.get("total_time", [""])[idx]),
        "servings": str(_value_or_default(df.get("servings", [""])[idx], "")),
        "nutrition_normalized": _value_or_default(df.get("nutrition_normalized", [{}])[idx], {}),
        "dietary_labels": _value_or_default(df.get("dietary_labels", [""])[idx], ""),
        "allergens": _value_or_default(df.get("allergens", [""])[idx], ""),
        "substitutions": _value_or_default(df.get("substitutions", [{}])[idx], {}),
        "health_tags": _value_or_default(df.get("health_tags", [""])[idx], ""),
    }


def _matches_filters(
    metadata: Dict[str, Any],
    dietary_filter: Optional[str],
    exclude_allergens: Optional[Iterable[str]],
    health_condition: Optional[str],
) -> bool:
    if dietary_filter and dietary_filter.lower() != "none":
        labels = " ".join(_to_text_list(metadata.get("dietary_labels")))
        if dietary_filter.lower() not in labels:
            return False

    if exclude_allergens:
        allergens = [a.strip().lower() for a in exclude_allergens if a and str(a).strip()]
        allergens_text = " ".join(_to_text_list(metadata.get("allergens")))
        ingredients_text = str(metadata.get("ingredients", "")).lower()
        if any(a in allergens_text or a in ingredients_text for a in allergens):
            return False

    if health_condition and health_condition.lower() != "none":
        # If health_tags is missing/empty in source data, do not block retrieval.
        tags = " ".join(_to_text_list(metadata.get("health_tags"))).strip()
        if tags and health_condition.lower() not in tags:
            return False


    return True


class _EndeeClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("ENDEE_URL", "http://localhost:8080").rstrip("/")
        self.api_prefix = "/api/v1"
        if self.base_url.endswith(self.api_prefix):
            self.base_url = self.base_url[: -len(self.api_prefix)].rstrip("/")
        self.auth_token = os.getenv("ENDEE_AUTH_TOKEN", "").strip()
        self.index_name = os.getenv("ENDEE_INDEX_NAME", "recipes").strip() or "recipes"
        self.space_type = os.getenv("ENDEE_SPACE_TYPE", "cosine").strip() or "cosine"
        self.precision = os.getenv("ENDEE_PRECISION", "int16").strip() or "int16"
        self.rebuild_index = os.getenv("ENDEE_REBUILD_INDEX", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
        }

    def _headers(self, content_type: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if self.auth_token:
            headers["Authorization"] = self.auth_token
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        normalized_path = path if path.startswith("/") else f"/{path}"
        if normalized_path == self.api_prefix:
            normalized_path = "/"
        elif normalized_path.startswith(f"{self.api_prefix}/"):
            normalized_path = normalized_path[len(self.api_prefix) :]
        url = f"{self.base_url}{self.api_prefix}{normalized_path}"
        timeout = kwargs.pop("timeout", 60)
        response = requests.request(method=method, url=url, timeout=timeout, **kwargs)
        return response

    def _list_indexes(self) -> List[Dict[str, Any]]:
        response = self._request("GET", "/api/v1/index/list", headers=self._headers())
        if response.status_code != 200:
            raise RuntimeError(
                f"Endee index list failed ({response.status_code}): {response.text[:400]}"
            )
        payload = response.json()
        return payload.get("indexes", [])

    def _delete_index(self) -> None:
        response = self._request(
            "DELETE",
            f"/api/v1/index/{self.index_name}/delete",
            headers=self._headers(),
        )
        # 404 means nothing to delete, which is fine
        if response.status_code not in (200, 404):
            raise RuntimeError(
                f"Endee index delete failed ({response.status_code}): {response.text[:400]}"
            )

    def _create_index(self, dimension: int) -> None:
        payload = {
            "index_name": self.index_name,
            "dim": int(dimension),
            "space_type": self.space_type,
            "precision": self.precision,
        }
        response = self._request(
            "POST",
            "/api/v1/index/create",
            headers=self._headers("application/json"),
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Endee index create failed ({response.status_code}): {response.text[:400]}"
            )

    def ensure_index(self, dimension: int) -> None:
        indexes = self._list_indexes()
        current = next((idx for idx in indexes if idx.get("name") == self.index_name), None)

        if self.rebuild_index:
            if current is not None:
                self._delete_index()
            self._create_index(dimension)
            return

        if current is None:
            self._create_index(dimension)
            return

        existing_dim = int(current.get("dimension", 0))
        if existing_dim != int(dimension):
            self._delete_index()
            self._create_index(dimension)

    def insert_vectors(self, vectors: List[Dict[str, Any]], batch_size: int = 200) -> None:
        for start in range(0, len(vectors), batch_size):
            batch = vectors[start : start + batch_size]
            response = self._request(
                "POST",
                f"/api/v1/index/{self.index_name}/vector/insert",
                headers=self._headers("application/json"),
                json=batch,
                timeout=120,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Endee vector insert failed ({response.status_code}): {response.text[:400]}"
                )

    def search(self, query_embedding: List[float], k: int) -> List[Dict[str, Any]]:
        if msgpack is None:
            raise RuntimeError("msgpack dependency is required for Endee search responses")

        payload = {
            "vector": query_embedding,
            "k": int(k),
            "include_vectors": False,
        }
        response = self._request(
            "POST",
            f"/api/v1/index/{self.index_name}/search",
            headers=self._headers("application/json"),
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Endee search failed ({response.status_code}): {response.text[:400]}"
            )

        unpacked = msgpack.unpackb(response.content, raw=False)

        # Most expected shape: {"results": [{...}, ...]}
        if isinstance(unpacked, dict):
            if "results" in unpacked and isinstance(unpacked["results"], list):
                return unpacked["results"]
            # Rare shape fallback: map-like payload with numeric keys
            for value in unpacked.values():
                if isinstance(value, list):
                    return value
        elif isinstance(unpacked, list):
            return unpacked

        return []


# ----- Chroma fallback -----
_chroma_client = None
_chroma_collection = None

if VECTOR_DB_BACKEND == "chroma":
    try:
        import chromadb

        chroma_path = os.getenv("CHROMA_PATH", ".chromadb")
        chroma_collection_name = os.getenv("CHROMA_COLLECTION", "recipes")
        _chroma_client = chromadb.PersistentClient(path=chroma_path)
        _chroma_collection = _chroma_client.get_or_create_collection(name=chroma_collection_name)
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "VECTOR_DB_BACKEND=chroma but ChromaDB initialization failed"
        ) from exc


_endee_client = _EndeeClient() if VECTOR_DB_BACKEND == "endee" else None


def build_vectorstore(df: pd.DataFrame, embeddings: List[List[float]]) -> None:
    """Build vector store in the configured backend from precomputed embeddings."""
    if not embeddings:
        raise ValueError("No embeddings provided to build_vectorstore")

    if VECTOR_DB_BACKEND == "endee":
        if _endee_client is None:
            raise RuntimeError("Endee client is not initialized")

        dim = len(embeddings[0])
        _endee_client.ensure_index(dim)

        payload: List[Dict[str, Any]] = []
        for idx, emb in enumerate(embeddings):
            metadata = _build_metadata(df, idx)
            filter_doc = {
                "dietary_labels": _to_text_list(metadata.get("dietary_labels")),
                "allergens": _to_text_list(metadata.get("allergens")),
                "health_tags": _to_text_list(metadata.get("health_tags")),
            }
            payload.append(
                {
                    "id": str(idx),
                    "vector": emb,
                    "meta": json.dumps(metadata, ensure_ascii=True),
                    "filter": json.dumps(filter_doc, ensure_ascii=True),
                }
            )

        _endee_client.insert_vectors(payload)
        return

    # Chroma fallback path
    if _chroma_client is None or _chroma_collection is None:
        raise RuntimeError("Chroma backend not initialized")

    # Recreate collection to avoid duplicate ID conflicts on startup re-hydration
    collection_name = _chroma_collection.name
    try:
        _chroma_client.delete_collection(name=collection_name)
    except Exception:
        pass
    collection = _chroma_client.get_or_create_collection(name=collection_name)

    for idx, emb in enumerate(embeddings):
        metadata = _build_metadata(df, idx)
        # Chroma metadata values should be scalar values
        chroma_metadata = {
            "recipe_name": str(metadata["recipe_name"]),
            "ingredients": str(metadata["ingredients"]),
            "directions": str(metadata["directions"]),
            "prep_time": str(metadata["prep_time"]),
            "cook_time": str(metadata["cook_time"]),
            "total_time": str(metadata["total_time"]),
            "servings": str(metadata["servings"]),
            "nutrition_normalized": json.dumps(metadata["nutrition_normalized"]),
            "dietary_labels": json.dumps(metadata["dietary_labels"]),
            "allergens": json.dumps(metadata["allergens"]),
            "substitutions": json.dumps(metadata["substitutions"]),
            "health_tags": json.dumps(metadata["health_tags"]),
        }
        collection.add(
            ids=[str(idx)],
            documents=[str(df["chunk"].iloc[idx])],
            metadatas=[chroma_metadata],
            embeddings=[emb],
        )


def _decode_endee_metadata(meta_value: Any, fallback_filter: Any = None) -> Dict[str, Any]:
    meta_text = ""

    if isinstance(meta_value, (bytes, bytearray)):
        meta_text = bytes(meta_value).decode("utf-8", errors="ignore")
    elif isinstance(meta_value, list) and all(isinstance(x, int) for x in meta_value):
        meta_text = bytes(meta_value).decode("utf-8", errors="ignore")
    elif isinstance(meta_value, str):
        meta_text = meta_value

    if meta_text:
        try:
            payload = json.loads(meta_text)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

    if isinstance(fallback_filter, str) and fallback_filter:
        try:
            filter_payload = json.loads(fallback_filter)
            if isinstance(filter_payload, dict):
                return filter_payload
        except Exception:
            pass

    return {}


def retrieve(
    query_embedding: List[float],
    k: int = 5,
    dietary_filter: Optional[str] = None,
    exclude_allergens: Optional[Iterable[str]] = None,
    health_condition: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve top-k recipes based on embedding similarity with optional filters."""
    if VECTOR_DB_BACKEND == "endee":
        if _endee_client is None:
            raise RuntimeError("Endee client is not initialized")

        # Fetch extra results first and then apply local dietary/allergen filters.
        raw_results = _endee_client.search(query_embedding=query_embedding, k=max(k * 4, 20))

        filtered: List[Dict[str, Any]] = []
        for item in raw_results:
            if isinstance(item, dict):
                meta_value = item.get("meta")
                filter_value = item.get("filter")
            elif isinstance(item, list):
                # Struct order: similarity, id, meta, filter, norm, vector
                meta_value = item[2] if len(item) > 2 else None
                filter_value = item[3] if len(item) > 3 else None
            else:
                continue

            metadata = _decode_endee_metadata(meta_value, filter_value)
            if not metadata:
                continue

            if _matches_filters(metadata, dietary_filter, exclude_allergens, health_condition):
                filtered.append(metadata)
                if len(filtered) >= k:
                    break

        return filtered

    if _chroma_collection is None:
        raise RuntimeError("Chroma backend not initialized")

    results = _chroma_collection.query(query_embeddings=[query_embedding], n_results=max(k * 4, 20))

    filtered: List[Dict[str, Any]] = []
    for metadata in results.get("metadatas", [[]])[0]:
        normalized = {
            "recipe_name": metadata.get("recipe_name", ""),
            "ingredients": metadata.get("ingredients", ""),
            "directions": metadata.get("directions", ""),
            "prep_time": metadata.get("prep_time", ""),
            "cook_time": metadata.get("cook_time", ""),
            "total_time": metadata.get("total_time", ""),
            "servings": metadata.get("servings", ""),
            "nutrition_normalized": metadata.get("nutrition_normalized", "{}"),
            "dietary_labels": metadata.get("dietary_labels", ""),
            "allergens": metadata.get("allergens", ""),
            "substitutions": metadata.get("substitutions", "{}"),
            "health_tags": metadata.get("health_tags", ""),
        }
        if _matches_filters(normalized, dietary_filter, exclude_allergens, health_condition):
            filtered.append(normalized)
            if len(filtered) >= k:
                break

    return filtered
