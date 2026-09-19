# image_generator.py
import io
import json
import os
import socket
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

HF_MODEL = "black-forest-labs/FLUX.1-schnell"
HF_URL   = f"https://api-inference.huggingface.co/models/{HF_MODEL}"


def _base_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_hf_token() -> str:
    token = (
        os.getenv("HF_TOKEN")
        or os.getenv("HUGGINGFACE_API_KEY")
        or os.getenv("HF_API_KEY")
        or ""
    ).strip()
    if token:
        return token

    config_path = _base_dir() / "config" / "api_keys.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    return str(
        data.get("hf_token")
        or data.get("huggingface_api_key")
        or data.get("hf_api_key")
        or ""
    ).strip()


def _looks_like_image(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") or data.startswith(b"RIFF")


def _generate(prompt: str, width: int = 1024, height: int = 1024) -> bytes:
    token = _load_hf_token()
    if not token:
        raise RuntimeError(
            "Hugging Face token missing. Add HF_TOKEN environment variable or "
            "'hf_token' in config/api_keys.json."
        )

    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(provider="auto", api_key=token)
        image = client.text_to_image(
            prompt,
            model=HF_MODEL,
            width=width,
            height=height,
            num_inference_steps=4,
            guidance_scale=0.0,
        )
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    except ModuleNotFoundError:
        print("[ImageGen] huggingface_hub not installed; using legacy HTTP fallback.")
    except Exception as e:
        message = str(e)
        if isinstance(e, (socket.gaierror, urllib.error.URLError)) or "getaddrinfo" in message:
            raise RuntimeError(
                "Network/DNS error while connecting to Hugging Face. "
                "Internet, DNS, VPN/proxy, or firewall may be blocking it."
            ) from e
        print(f"[ImageGen] InferenceClient failed; trying legacy HTTP fallback: {e}")

    payload = json.dumps({
        "inputs": prompt,
        "parameters": {
            "width":               width,
            "height":              height,
            "num_inference_steps": 4,
            "guidance_scale":      0.0,
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        HF_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "image/png",
        },
        method="POST",
    )

    # Retry up to 3 times; model may be loading (503)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
                if not _looks_like_image(data):
                    text = data.decode("utf-8", errors="ignore")
                    raise RuntimeError(f"HF returned non-image response: {text[:200]}")
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 503 and "loading" in body.lower() and attempt < 2:
                wait = 20 + attempt * 10
                print(f"[ImageGen] Model loading - waiting {wait}s (attempt {attempt+1})")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HF API error {e.code}: {body[:200]}")
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.gaierror):
                raise RuntimeError(
                    "Network/DNS error while connecting to Hugging Face. "
                    "Internet, DNS, VPN/proxy, or firewall may be blocking it."
                ) from e
            raise

    raise RuntimeError("HF model did not load after 3 attempts.")


def image_generator(
    parameters:    dict,
    response=None,
    player=None,
    speak=None,
) -> str:
    params  = parameters or {}
    prompt  = (params.get("prompt") or "").strip()
    style   = (params.get("style")  or "").strip()
    width   = int(params.get("width",  1024))
    height  = int(params.get("height", 1024))
    save_to = (params.get("save_to") or "").strip()

    if not prompt:
        return "Sir, prompt dena padega - kya generate karna hai?"

    # Attach style if given
    full_prompt = f"{prompt}, {style}" if style else prompt

    # Clamp dimensions to valid values
    width  = max(256, min(1440, width))
    height = max(256, min(1440, height))

    if player:
        player.write_log(f"[ImageGen] Generating: {full_prompt[:50]}")
    print(f"[ImageGen] Prompt: {full_prompt!r}  size={width}x{height}")

    try:
        img_bytes = _generate(full_prompt, width, height)
    except Exception as e:
        print(f"[ImageGen] ERROR: {e}")
        return f"Image generation failed sir: {e}"

    # Save path
    if not save_to:
        desktop  = Path.home() / "Desktop"
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_to  = str(desktop / f"jarvis_image_{ts}.png")

    try:
        path = Path(save_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img_bytes)
    except Exception as e:
        return f"Image generated but save failed: {e}"

    # Open the image
    try:
        import subprocess, platform
        if platform.system() == "Windows":
            subprocess.Popen(["start", "", save_to], shell=True)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", save_to])
        else:
            subprocess.Popen(["xdg-open", save_to])
    except Exception:
        pass

    print(f"[ImageGen] Saved: {save_to}")
    if player:
        player.write_log(f"[ImageGen] Saved: {Path(save_to).name}")

    return f"Image ready sir - saved at: {save_to}"
