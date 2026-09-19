# actions/ai_reasoning.py
import os
from core.ai_providers import get_router

def ai_reasoning(parameters: dict, player=None, speak=None) -> str:
    """
    Advanced mathematical solving, logical reasoning, and deep research.
    parameters:
      action: solve_math | logical_reasoning | deep_research
      query: the math equation, logic puzzle, or research topic (required)
      steps: boolean, show step-by-step reasoning (default True)
    """
    action = parameters.get("action", "").lower().strip()
    query = parameters.get("query", "").strip()
    
    if not action:
        return "No action specified."
    if not query:
        return "Please provide a query for reasoning/math."
        
    if player:
        player.write_log(f"AI Reasoner: {action}")
        
    try:
        router = get_router()
        show_steps = parameters.get("steps", True)
        
        if action == "solve_math":
            if speak:
                speak("Sir, solving math problem using DeepSeek R1.")
                
            prompt = (
                f"You are a math solver. Solve the following mathematical equation/problem with extreme precision. "
                f"Show your step-by-step calculations and explain the logic clearly. Finally, box the final answer.\n\n"
                f"Problem: {query}"
            )
            # Route to deepseek-r1:free (math strong reasoning model)
            result = router.generate_text(prompt, task_type="reasoning")
            return f"Solution:\n{result}"
            
        elif action == "logical_reasoning":
            if speak:
                speak("Sir, analyzing logic problem.")
                
            prompt = (
                f"Analyze the following logic puzzle/problem and explain the logical reasoning behind the solution. "
                f"Reason step-by-step to arrive at the correct conclusion:\n\n"
                f"Puzzle: {query}"
            )
            result = router.generate_text(prompt, task_type="reasoning")
            return f"Logical Deduction:\n{result}"
            
        elif action == "deep_research":
            if speak:
                speak("Sir, starting deep research on this topic.")
                
            prompt = (
                f"Perform a comprehensive research and detailed analysis of the following topic. "
                f"Organize into sections with introductions, deep technical details, comparisons, and conclusions:\n\n"
                f"Topic: {query}"
            )
            # Route to nemotron-3-ultra-550b-a55b:free for deep research
            result = router.generate_text(prompt, task_type="reasoning")
            return f"Research Report:\n{result}"
            
        else:
            return f"Unknown AI Reasoning action: {action}"
            
    except Exception as e:
        return f"AI Reasoning action failed: {e}"
