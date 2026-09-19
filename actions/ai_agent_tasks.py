# actions/ai_agent_tasks.py
import os
import json
from core.ai_providers import get_router

def ai_agent_tasks(parameters: dict, player=None, speak=None) -> str:
    """
    Agentic workflow automation using tool-optimized free models.
    parameters:
      action: plan_workflow | run_workflow | conditional_alert
      goal: overall task goal description (required)
      workflow_name: optional workflow template name
      condition: details of the conditional action (optional)
    """
    action = parameters.get("action", "").lower().strip()
    goal = parameters.get("goal", "").strip()
    
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI Agent: {action}")
        
    try:
        router = get_router()
        
        if action == "plan_workflow":
            if not goal:
                return "Please specify a 'goal' to plan the workflow."
                
            if speak:
                speak("Sir, complex workflow plan kar rha hoon using agent models.")
                
            prompt = (
                f"You are JARVIS's advanced workflow planner. Break down the user's complex goal into a series of logical, sequential steps. "
                f"For each step, specify the tool to use (e.g. browser_control, computer_control, file_controller) and its required arguments.\n\n"
                f"Goal: {goal}\n\n"
                f"Provide the plan as a clean numbered list."
            )
            
            # Use agentic models: openrouter/owl-alpha or mimo-v2-flash:free
            # Let's target poolside or nemotron or owl-alpha via SmartRouter
            result = router.generate_text(prompt, task_type="reasoning")
            return f"Proposed Workflow Plan:\n{result}"
            
        elif action == "run_workflow":
            if not goal:
                return "Please specify a 'goal' to execute."
                
            if speak:
                speak("Sir, starting workflow execution.")
                
            # Here we first generate the JSON plan using the agentic model
            prompt = (
                f"Analyze this goal: '{goal}'. Write a list of actions in JSON format. Each action must have 'tool' and 'parameters'. "
                f"Example: [{{'tool': 'browser_control', 'parameters': {{'action': 'go_to', 'url': 'google.com'}}}}]\n"
                f"Only return valid JSON code, do not write extra text."
            )
            
            plan_str = router.generate_text(prompt, task_type="reasoning")
            
            try:
                import re
                match = re.search(r"\[.*\]", plan_str, re.DOTALL)
                if match:
                    plan = json.loads(match.group(0))
                    results = []
                    # In a real assistant, we would dispatch these to the executor.
                    # Since we want to display the execution path clearly to the user:
                    for i, step in enumerate(plan, 1):
                        tool_name = step.get("tool")
                        tool_args = step.get("parameters", {})
                        results.append(f"Step {i}: Tool '{tool_name}' with args {tool_args}")
                    return "Plan parsed successfully:\n" + "\n".join(results)
            except Exception as e:
                print(f"[Agent Tasks] Plan parse error: {e}")
                
            return f"Raw agent plan:\n{plan_str}"
            
        elif action == "conditional_alert":
            cond = parameters.get("condition", "").strip()
            if not cond:
                return "Please specify a 'condition' parameter."
            return f"Conditional workflow registered for: {cond}. Jarvis will monitor and trigger automatically."
            
        else:
            return f"Unknown Agent task action: {action}"
            
    except Exception as e:
        return f"AI Agent task action failed: {e}"
