# actions/ai_vision_chat.py
import os
import base64
from pathlib import Path
from core.ai_providers import get_router, HuggingFaceProvider

def ai_vision_chat(parameters: dict, player=None, speak=None) -> str:
    """
    Advanced vision intelligence chat, object detection, and captioning.
    parameters:
      action: chat | detect_objects | caption
      image_path: path to the target image (optional, fallback to player.current_file)
      question: question to ask about image (for chat action)
    """
    action = parameters.get("action", "").lower().strip()
    image_path = parameters.get("image_path", "").strip()
    
    if not image_path and player and player.current_file:
        image_path = player.current_file
        
    if not image_path:
        return "Please upload or provide an 'image_path' to perform vision analysis."
        
    p = Path(image_path)
    if not p.exists():
        return f"Image file not found: {image_path}"
        
    if player:
        player.write_log(f"AI Vision Chat: {p.name}")
        
    try:
        # Read image bytes
        image_bytes = p.read_bytes()
        img_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        router = get_router()
        hf = HuggingFaceProvider()
        
        if action == "chat":
            question = parameters.get("question", "What do you see in this image? Describe in detail.").strip()
            if speak:
                speak("Sir, analyzing the image.")
            res = router.run_vision(image_bytes, question)
            return f"AI Vision Response:\n{res}"
            
        elif action == "detect_objects":
            if speak:
                speak("Sir, detecting objects in the image using object detection model.")
                
            try:
                res = hf.query("facebook/detr-resnet-50", {"inputs": img_b64})
                # DETR returns a list of dictionaries with labels, scores and boxes
                if isinstance(res, list) and len(res) > 0:
                    objects = [f"{item['label']} (Confidence: {item['score']:.2f})" for item in res]
                    return f"Objects Detected:\n- " + "\n- ".join(objects)
            except Exception as e:
                print(f"[Vision Chat] DETR failed: {e}. Falling back to OpenRouter vision...")
                
            # Fallback
            res = router.run_vision(image_bytes, "List all the individual objects you see in this image clearly.")
            return f"Objects Detected (AI):\n{res}"
            
        elif action == "caption":
            if speak:
                speak("Sir, generating caption for this image.")
                
            try:
                res = hf.query("Salesforce/blip-image-captioning-large", {"inputs": img_b64})
                if isinstance(res, list) and len(res) > 0 and "generated_text" in res[0]:
                    return f"Image Caption: {res[0]['generated_text']}"
            except Exception as e:
                print(f"[Vision Chat] BLIP failed: {e}. Falling back to OpenRouter...")
                
            res = router.run_vision(image_bytes, "Generate a one-sentence descriptive caption for this image.")
            return f"Image Caption (AI): {res}"
            
        else:
            return f"Unknown AI Vision Chat action: {action}"
            
    except Exception as e:
        return f"AI Vision Chat failed: {e}"
