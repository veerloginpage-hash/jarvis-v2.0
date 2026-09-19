# actions/ai_embeddings.py
import os
import json
from pathlib import Path
from core.ai_providers import HuggingFaceProvider

def get_embedding(hf_provider, text: str) -> list:
    """Gets text embedding from BAAI/bge-large-en-v1.5 via HuggingFace."""
    # We clean and limit input size for the API
    clean_text = text.replace("\n", " ")[:1000]
    payload = {"inputs": clean_text}
    res = hf_provider.query("BAAI/bge-large-en-v1.5", payload)
    
    # BGE model returns list of floats (embedding vector)
    if isinstance(res, list) and len(res) > 0:
        if isinstance(res[0], float):
            return res
        elif isinstance(res[0], list):
            return res[0]
    raise Exception(f"Failed to get embedding: {res}")

def cosine_similarity(v1, v2):
    dot = sum(x * y for x, y in zip(v1, v2))
    mag1 = sum(x * x for x in v1) ** 0.5
    mag2 = sum(x * x for x in v2) ** 0.5
    if not mag1 or not mag2:
        return 0.0
    return dot / (mag1 * mag2)

def ai_embeddings(parameters: dict, player=None, speak=None) -> str:
    """
    Semantic search across documents, files, and directories.
    parameters:
      action: search_files | search_notes
      query: semantic search query (required)
      path: root path to search in (optional, default Desktop)
      extension: file extension filter e.g. txt, py, md
    """
    action = parameters.get("action", "").lower().strip()
    query = parameters.get("query", "").strip()
    
    if not action:
        return "No action specified."
    if not query:
        return "Please specify a search 'query'."
        
    if player:
        player.write_log(f"Semantic Search: {query[:30]}")
        
    try:
        hf = HuggingFaceProvider()
        
        # 1. Get embedding for the query
        if speak:
            speak(f"Sir, semantic embedding fetch kar rha hoon for search query.")
        query_emb = get_embedding(hf, query)
        
        # Determine files to search
        search_path_str = parameters.get("path", "").strip()
        if not search_path_str:
            root_dir = Path.home() / "Desktop"
        else:
            root_dir = Path(search_path_str)
            
        if not root_dir.exists():
            return f"Search path does not exist: {root_dir}"
            
        ext_filter = parameters.get("extension", "").strip().lower().lstrip(".")
        
        # Gather text files
        text_files = []
        exts = [f"*.{ext_filter}"] if ext_filter else ["*.txt", "*.md", "*.py", "*.json"]
        
        for ext in exts:
            text_files.extend(list(root_dir.glob(ext)))
            
        if not text_files:
            return f"No matching text files found in {root_dir}."
            
        if speak:
            speak(f"Sir, total {len(text_files)} files scanning start ho chuka hai.")
            
        # Get embeddings for each file's content
        scores = []
        for file in text_files[:15]: # Limit to top 15 files to prevent API rate limiting
            try:
                content = file.read_text(encoding="utf-8", errors="ignore").strip()
                if not content:
                    continue
                file_emb = get_embedding(hf, content[:800])
                sim = cosine_similarity(query_emb, file_emb)
                scores.append((file.name, sim, file.absolute()))
            except Exception as e:
                print(f"[AI Embeddings] Skipping file {file.name}: {e}")
                
        # Sort by similarity score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        
        results = [f"• {name} (Similarity: {score:.2f})\n  Path: {path}" for name, score, path in scores[:5]]
        
        if not results:
            return "No semantically matching files found."
            
        return "Semantic Search Results:\n" + "\n".join(results)
        
    except Exception as e:
        return f"AI Semantic Search action failed: {e}"
