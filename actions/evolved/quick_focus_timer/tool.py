import time

def run(parameters: dict, player=None, speak=None) -> str:
    """Starts a focus timer for a specified number of minutes and notifies the user when it's done."""
    minutes = parameters.get("minutes")

    if not isinstance(minutes, int) or minutes <= 0:
        return "Error: 'minutes' must be a positive integer."

    seconds = minutes * 60
    
    if speak:
        speak(f"Starting a {minutes}-minute focus timer. I will notify you when it's done.")
    
    # Use player.send_message if player exists, otherwise print for headless ops
    if player:
        player.send_message(f"Starting a {minutes}-minute focus timer.")
    else:
        print(f"Starting a {minutes}-minute focus timer.")

    time.sleep(seconds)
    
    if speak:
        speak(f"Your {minutes}-minute focus session is complete!")

    if player:
        player.send_message(f"Your {minutes}-minute focus session is complete!")
    else:
        print(f"Your {minutes}-minute focus session is complete!")

    return f"Focus timer for {minutes} minutes finished."