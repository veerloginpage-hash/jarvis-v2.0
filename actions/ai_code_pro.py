# actions/ai_code_pro.py
import os
import json
from pathlib import Path
from core.ai_providers import get_router, HuggingFaceProvider

def ai_code_pro(parameters: dict, player=None, speak=None) -> str:
    """
    Pro-level AI Code generation, review, and SQL assistance.
    parameters:
      action: generate | review | sql_gen | project_refactor
      description: prompt or requirements (required)
      language: python | javascript | html | etc.
      file_path: target file path for review or refactor (optional)
      schema: database schema context (for sql_gen, optional)
    """
    action = parameters.get("action", "").lower().strip()
    desc = parameters.get("description", "").strip()
    
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Code: {action}")
        
    try:
        router = get_router()
        
        if action == "generate":
            if not desc:
                return "Please provide a description of the code you want to generate."
                
            lang = parameters.get("language", "python").strip()
            if speak:
                speak(f"Sir, coding start kar rha hoon in {lang} using Qwen Coder.")
                
            prompt = (
                f"Write clean, optimized, and well-commented code in {lang} for the following request:\n"
                f"Request: {desc}\n\n"
                f"Return the code inside standard markdown code blocks."
            )
            
            # Route to qwen3-coder:free for best coding results
            result = router.generate_text(prompt, task_type="coding")
            return result
            
        elif action == "review":
            file_path = parameters.get("file_path", "").strip()
            if not file_path:
                if player and player.current_file:
                    file_path = player.current_file
                else:
                    return "Please provide a 'file_path' of the code file to review."
                    
            p = Path(file_path)
            if not p.exists():
                return f"File not found: {file_path}"
                
            code_content = p.read_text(encoding="utf-8", errors="ignore")
            
            if speak:
                speak(f"Sir, code file review kar rha hoon for bugs and improvements.")
                
            prompt = (
                f"Perform a professional code review of the following code. Identify bugs, performance bottlenecks, "
                f"security concerns, and areas for code quality improvement:\n\n"
                f"File: {p.name}\n"
                f"Code:\n```\n{code_content}\n```"
            )
            
            # Use DeepSeek R1 for reasoning/reviewing
            result = router.generate_text(prompt, task_type="reasoning")
            return result
            
        elif action == "sql_gen":
            if not desc:
                return "Please provide a natural language query description."
                
            schema = parameters.get("schema", "").strip()
            
            if speak:
                speak("Sir, SQL query generate kar rha hoon.")
                
            # Attempt to use defog/sqlcoder-7b-2 via HF, fallback to OpenRouter coding
            try:
                hf = HuggingFaceProvider()
                payload = {
                    "inputs": f"Schema: {schema}\nQuery: {desc}\nSQL:"
                }
                res = hf.query("defog/sqlcoder-7b-2", payload)
                if isinstance(res, list) and len(res) > 0 and "generated_text" in res[0]:
                    return f"Generated SQL:\n{res[0]['generated_text']}"
            except Exception as e:
                print(f"[SQL Coder] HF sqlcoder failed: {e}. Falling back to OpenRouter...")
                
            prompt = (
                f"Given the database schema:\n{schema or 'Default'}\n\n"
                f"Generate a clean and correct SQL query for: '{desc}'\n"
                f"Return ONLY the SQL query."
            )
            result = router.generate_text(prompt, task_type="coding")
            return result
            
        elif action == "project_refactor":
            # Multi-file analysis or large code refactoring using Qwen Coder
            file_path = parameters.get("file_path", "").strip()
            if not file_path:
                return "Please specify a file_path or directory to refactor."
                
            p = Path(file_path)
            if not p.exists():
                return f"Path not found: {file_path}"
                
            content = p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""
            
            prompt = (
                f"Refactor the following code/project files according to this request: '{desc}'\n\n"
                f"File Content:\n```\n{content}\n```"
            )
            
            if speak:
                speak("Sir, code refactoring start kar rha hoon.")
                
            result = router.generate_text(prompt, task_type="coding")
            return result
            
        else:
            return f"Unknown AI Code action: {action}"
            
    except Exception as e:
        return f"AI Code action failed: {e}"
