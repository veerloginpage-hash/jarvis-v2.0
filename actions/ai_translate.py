# actions/ai_translate.py
import os
from pathlib import Path
from core.ai_providers import get_router
from actions.screen_processor import _capture_screen
from actions.ai_screen_vision import run_florence_ocr
from actions.ai_document import extract_pdf_text_helper, extract_docx_text_helper

def ai_translate(parameters: dict, player=None, speak=None) -> str:
    """
    Advanced translation utility powered by free LLMs.
    parameters:
      action: translate_text | translate_screen | translate_document
      text: text to translate (required for translate_text)
      target_lang: target language, e.g. Hindi, Spanish, English (default English)
      file_path: document file path (for translate_document)
    """
    action = parameters.get("action", "").lower().strip()
    target_lang = parameters.get("target_lang", "English").strip()
    
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Translate: {action}")
        
    try:
        router = get_router()
        
        if action == "translate_text":
            text = parameters.get("text", "").strip()
            if not text:
                return "Please provide 'text' to translate."
                
            if speak:
                speak(f"Sir, translating to {target_lang}.")
                
            prompt = (
                f"You are a professional translator. Translate the following text into {target_lang} precisely, "
                f"preserving the original meaning, tone, and formatting. Return ONLY the translation:\n\n"
                f"{text}"
            )
            
            result = router.generate_text(prompt, task_type="general")
            return result
            
        elif action == "translate_screen":
            if speak:
                speak(f"Sir, screen content scan aur {target_lang} me translate kar rha hoon.")
                
            # Capture screen
            img_bytes, mime = _capture_screen()
            # Perform OCR
            screen_text = run_florence_ocr(img_bytes)
            if not screen_text.strip():
                return "No text detected on the screen to translate."
                
            prompt = (
                f"Translate the following text extracted from a screen capture into {target_lang}. "
                f"Ensure the translation matches the natural structure. Extracted text:\n\n"
                f"{screen_text}"
            )
            result = router.generate_text(prompt, task_type="general")
            return f"Translated Screen Content:\n{result}"
            
        elif action == "translate_document":
            file_path = parameters.get("file_path", "").strip()
            if not file_path and player and player.current_file:
                file_path = player.current_file
                
            if not file_path:
                return "Please provide a 'file_path' of the document to translate."
                
            p = Path(file_path)
            if not p.exists():
                return f"File not found: {file_path}"
                
            ext = p.suffix.lower()
            text_content = ""
            if ext == ".pdf":
                text_content = extract_pdf_text_helper(p)
            elif ext in (".docx", ".doc"):
                text_content = extract_docx_text_helper(p)
            else:
                text_content = p.read_text(encoding="utf-8", errors="ignore")
                
            if not text_content.strip():
                return "Could not read text from document."
                
            if speak:
                speak(f"Sir, document translate kar rha hoon into {target_lang}.")
                
            prompt = (
                f"Translate the following document content into {target_lang}. Preserve key structures:\n\n"
                f"{text_content[:30000]}"
            )
            result = router.generate_text(prompt, task_type="general")
            
            # Save translated file
            save_name = f"{p.stem}_translated_{target_lang}.txt"
            save_path = p.parent / save_name
            save_path.write_text(result, encoding="utf-8")
            
            return f"Document translated successfully and saved at:\n{save_path}\n\nSummary:\n{result[:400]}..."
            
        else:
            return f"Unknown translation action: {action}"
            
    except Exception as e:
        return f"Translation action failed: {e}"
