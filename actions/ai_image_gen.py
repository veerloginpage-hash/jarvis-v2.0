# actions/ai_image_gen.py
import os
import time
import io
import json
import subprocess
import platform
from pathlib import Path
from datetime import datetime
from core.ai_providers import HuggingFaceProvider
from actions.image_generator import _looks_like_image

def ai_image_generator(parameters: dict, player=None, speak=None) -> str:
    """
    Enhanced image generation action.
    parameters:
      prompt: what to generate (required)
      style: anime | realistic | cyberpunk | oil painting | etc.
      model_type: fast | quality | classic (default: fast)
      width: width in px (default 1024)
      height: height in px (default 1024)
      save_path: where to save (optional)
    """
    prompt = parameters.get("prompt", "").strip()
    if not prompt:
        return "Please specify a prompt for image generation."
        
    style = parameters.get("style", "").strip()
    if style:
        prompt = f"{prompt}, {style} style"
        
    model_type = parameters.get("model_type", "fast").lower().strip()
    width = int(parameters.get("width", 1024))
    height = int(parameters.get("height", 1024))
    
    # Map model type to HF model ids
    model_map = {
        "fast": "black-forest-labs/FLUX.1-schnell",
        "quality": "black-forest-labs/FLUX.1-dev",
        "classic": "stabilityai/stable-diffusion-xl-base-1.0"
    }
    model_id = model_map.get(model_type, model_map["fast"])
    
    if speak:
        speak(f"Sir, image generate kar rha hoon using {model_type} model: {prompt[:40]}")
    if player:
        player.write_log(f"ImageGen: {prompt[:30]}")
        
    try:
        # Use HuggingFace provider to fetch the binary image data
        hf = HuggingFaceProvider()
        
        # Schnell and Dev parameters
        payload = {
            "inputs": prompt,
            "parameters": {
                "width": max(256, min(1440, width)),
                "height": max(256, min(1440, height))
            }
        }
        
        # Query binary data
        img_bytes = hf.query(model_id, payload, is_binary=True)
        
        if not _looks_like_image(img_bytes):
            return "Hugging Face returned invalid image bytes."
            
        # Determine save path
        save_path = parameters.get("save_path", "").strip()
        if not save_path:
            desktop = Path.home() / "Desktop"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = str(desktop / f"jarvis_ai_image_{ts}.png")
            
        p = Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(img_bytes)
        
        # Automatically open the generated image
        try:
            if platform.system() == "Windows":
                subprocess.Popen(["start", "", save_path], shell=True)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", save_path])
            else:
                subprocess.Popen(["xdg-open", save_path])
        except Exception:
            pass
            
        return f"Image successfully generated and saved to {save_path}"
        
    except Exception as e:
        print(f"[AI Image Gen] Error: {e}")
        return f"Image generation failed: {e}"
