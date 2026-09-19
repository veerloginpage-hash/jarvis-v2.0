# actions/ai_audio.py
import io
import time
import subprocess
import platform
from pathlib import Path
from datetime import datetime
from core.ai_providers import HuggingFaceProvider

def ai_audio_generator(parameters: dict, player=None, speak=None) -> str:
    """
    Advanced AI Audio module.
    parameters:
      action: generate_music | transcribe_audio | text_to_speech
      prompt: description of music to generate, or text for speech
      file_path: input file path for transcription (optional)
      target_path: where to save generated audio (optional)
      voice: voice model (for TTS) - suno/bark or coqui/XTTS-v2
    """
    action = parameters.get("action", "").lower().strip()
    prompt = parameters.get("prompt", "").strip()
    
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Audio: {action}")
        
    try:
        hf = HuggingFaceProvider()
        
        if action == "generate_music":
            if not prompt:
                return "Please provide a prompt describing the music to generate."
                
            if speak:
                speak("Sir, AI music generate kar rha hoon. Please wait a moment.")
                
            payload = {"inputs": prompt}
            
            # Query facebook/musicgen-large for the audio file (binary response)
            audio_bytes = hf.query("facebook/musicgen-large", payload, is_binary=True)
            
            save_path = parameters.get("target_path", "").strip()
            if not save_path:
                desktop = Path.home() / "Desktop"
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = str(desktop / f"jarvis_music_{ts}.wav")
                
            p = Path(save_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(audio_bytes)
            
            # Automatically play the generated music
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(["start", "", save_path], shell=True)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", save_path])
                else:
                    subprocess.Popen(["xdg-open", save_path])
            except Exception:
                pass
                
            return f"Music successfully generated and saved to {save_path}"
            
        elif action == "transcribe_audio":
            file_path = parameters.get("file_path", "").strip()
            if not file_path:
                # If a file is uploaded to the UI, let's use it
                if player and player.current_file:
                    file_path = player.current_file
                else:
                    return "Please provide a file_path of the audio file to transcribe."
                    
            p = Path(file_path)
            if not p.exists():
                return f"Audio file not found: {file_path}"
                
            if speak:
                speak("Sir, audio file transcribe kar rha hoon using Whisper Large.")
                
            # Read file bytes
            audio_data = p.read_bytes()
            
            # Whisper large API call
            # We can send the binary audio file directly as a post body
            headers = {"Authorization": f"Bearer {hf.token}"}
            import requests
            url = f"https://api-inference.huggingface.co/models/openai/whisper-large-v3"
            
            response = requests.post(url, headers=headers, data=audio_data, timeout=60)
            if response.status_code == 200:
                result = response.json()
                text = result.get("text", "")
                return f"Transcription Results:\n{text}"
            else:
                return f"Whisper transcription failed with status code: {response.status_code}. Response: {response.text}"
                
        elif action == "text_to_speech":
            if not prompt:
                return "Please provide text to convert to speech."
                
            voice = parameters.get("voice", "suno/bark").strip().lower()
            model_id = "suno/bark" if "bark" in voice else "coqui/XTTS-v2"
            
            if speak:
                speak("Sir, speech generate kar rha hoon.")
                
            payload = {"inputs": prompt}
            audio_bytes = hf.query(model_id, payload, is_binary=True)
            
            save_path = parameters.get("target_path", "").strip()
            if not save_path:
                desktop = Path.home() / "Desktop"
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                save_path = str(desktop / f"jarvis_speech_{ts}.wav")
                
            p = Path(save_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(audio_bytes)
            
            try:
                if platform.system() == "Windows":
                    subprocess.Popen(["start", "", save_path], shell=True)
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", save_path])
                else:
                    subprocess.Popen(["xdg-open", save_path])
            except Exception:
                pass
                
            return f"Speech generated successfully: {save_path}"
            
        else:
            return f"Unknown AI Audio action: {action}"
            
    except Exception as e:
        return f"AI Audio action failed: {e}"
