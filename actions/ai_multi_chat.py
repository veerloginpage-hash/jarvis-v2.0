# actions/ai_multi_chat.py
import os
import json
from core.ai_providers import get_router, OpenRouterProvider

# Global model state
_current_active_model = "openrouter/free"

def ai_multi_chat(parameters: dict, player=None, speak=None) -> str:
    """
    Model switching, comparisons, and custom model routing.
    parameters:
      action: switch_model | compare_models | ask_model | get_active_model
      model_name: specific model to use or switch to (required for switch_model/ask_model)
      query: prompt or question (required for ask_model/compare_models)
    """
    global _current_active_model
    action = parameters.get("action", "").lower().strip()
    
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Multi-Chat: {action}")
        
    try:
        router = get_router()
        or_prov = OpenRouterProvider()
        
        # Mapping simple names to full OpenRouter Model IDs
        model_aliases = {
            "free": "openrouter/free",
            "owl": "openrouter/owl-alpha",
            "gemma": "google/gemma-4-31b-it:free",
            "poolside": "poolside/laguna-m.1:free",
            "dolphin": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
            "llama": "meta-llama/llama-3.2-3b-instruct:free",
        }
        
        if action == "switch_model":
            model_input = parameters.get("model_name", "").lower().strip()
            if not model_input:
                return "Please provide 'model_name' to switch to."
                
            model_id = model_aliases.get(model_input, model_input)
            
            # Simple validation to check if model has :free endpoint or is valid
            if not model_id.endswith(":free") and model_id != "openrouter/free" and model_id != "openrouter/owl-alpha":
                model_id = f"{model_id}:free"
                
            _current_active_model = model_id
            msg = f"Active chat model changed to: {_current_active_model}"
            if speak:
                speak(f"Sir, chat model switch ho gya hai to {model_input}.")
            return msg
            
        elif action == "ask_model":
            model_input = parameters.get("model_name", "").lower().strip()
            query = parameters.get("query", "").strip()
            if not model_input or not query:
                return "Model name and query are required."
                
            model_id = model_aliases.get(model_input, model_input)
            if not model_id.endswith(":free") and model_id != "openrouter/free" and model_id != "openrouter/owl-alpha":
                model_id = f"{model_id}:free"
                
            if speak:
                speak(f"Querying {model_input} model.")
                
            messages = [{"role": "user", "content": query}]
            res = or_prov.chat(model_id, messages)
            return f"Response from {model_id}:\n{res}"
            
        elif action == "compare_models":
            query = parameters.get("query", "").strip()
            if not query:
                return "Please provide a 'query' to compare models."
                
            models_to_test = [
                "openrouter/free",
                "openrouter/owl-alpha",
                "google/gemma-4-31b-it:free"
            ]
            
            if speak:
                speak("Sir, comparing response from OpenRouter Free, Owl Alpha, and Gemma 4.")
                
            messages = [{"role": "user", "content": query}]
            results = []
            
            for m in models_to_test:
                try:
                    res = or_prov.chat(m, messages)
                    # Get first paragraph or short snippet
                    snippet = res.split("\n\n")[0]
                    results.append(f"### {m}\n{snippet}\n")
                except Exception as e:
                    results.append(f"### {m}\nError: {e}\n")
                    
            return "Comparison Results:\n\n" + "\n".join(results)
            
        elif action == "get_active_model":
            return f"Currently active conversation model is: {_current_active_model}"
            
        else:
            return f"Unknown AI Multi-Chat action: {action}"
            
    except Exception as e:
        return f"AI Multi-Chat action failed: {e}"
