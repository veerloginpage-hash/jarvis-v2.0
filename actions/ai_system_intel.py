# actions/ai_system_intel.py
import os
import shutil
import time
from core.ai_providers import get_router

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

def get_system_stats() -> dict:
    stats = {}
    if _PSUTIL:
        stats["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        stats["memory_percent"] = psutil.virtual_memory().percent
        stats["memory_available_gb"] = psutil.virtual_memory().available / (1024 ** 3)
    else:
        stats["cpu_percent"] = "unknown"
        stats["memory_percent"] = "unknown"
        stats["memory_available_gb"] = "unknown"
        
    usage = shutil.disk_usage("/")
    stats["disk_percent"] = (usage.used / usage.total) * 100
    stats["disk_free_gb"] = usage.free / (1024 ** 3)
    return stats

def ai_system_intel(parameters: dict, player=None, speak=None) -> str:
    """
    AI system health diagnostic, process manager, and storage optimization advisor.
    parameters:
      action: diagnose | list_heavy_processes | storage_advice | clean_temp
    """
    action = parameters.get("action", "").lower().strip()
    if not action:
        return "No action specified."
        
    if player:
        player.write_log(f"AI System: {action}")
        
    try:
        router = get_router()
        stats = get_system_stats()
        
        if action == "diagnose":
            if speak:
                speak("Sir, computer health scan kar rha hoon. Recommendations compose kar rha hoon.")
                
            prompt = (
                f"Analyze the following real-time computer system stats and explain if the system is healthy, "
                f"identifying potential bottlenecks or optimizations. CPU load: {stats['cpu_percent']}%, "
                f"RAM usage: {stats['memory_percent']}%, available RAM: {stats['memory_available_gb']:.2f} GB, "
                f"disk usage: {stats['disk_percent']:.1f}%, free disk space: {stats['disk_free_gb']:.2f} GB. "
                f"Return a clean breakdown with action items if any."
            )
            result = router.generate_text(prompt, task_type="general")
            return f"System Diagnostics Report:\n{result}"
            
        elif action == "list_heavy_processes":
            if not _PSUTIL:
                return "psutil is not installed."
                
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    processes.append(proc.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
            # Sort processes by CPU usage descending
            processes.sort(key=lambda x: x.get('cpu_percent') or 0, reverse=True)
            top_cpu = processes[:5]
            
            # Sort by memory usage descending
            processes.sort(key=lambda x: x.get('memory_percent') or 0, reverse=True)
            top_mem = processes[:5]
            
            prompt = (
                f"Analyze these active running processes. Highlight any suspicious or highly demanding process that should "
                f"be closed to improve speed:\n"
                f"Top CPU Processes:\n{json.dumps(top_cpu)}\n"
                f"Top Memory Processes:\n{json.dumps(top_mem)}\n"
            )
            
            import json
            result = router.generate_text(prompt, task_type="general")
            return f"Heavy Processes Analysis:\n{result}"
            
        elif action == "storage_advice":
            if speak:
                speak("Sir, disk usage pattern analyze kar rha hoon.")
                
            # Read folder contents of some common directories (Downloads, Desktop, Documents)
            # Find largest files
            large_files = []
            scan_dirs = [Path.home() / "Downloads", Path.home() / "Desktop"]
            from pathlib import Path
            for sdir in scan_dirs:
                if sdir.exists():
                    for file in sdir.glob("*"):
                        if file.is_file():
                            try:
                                large_files.append((file.name, file.stat().st_size / (1024*1024), file.absolute()))
                            except Exception:
                                pass
            large_files.sort(key=lambda x: x[1], reverse=True)
            
            prompt = (
                f"Analyze this list of largest files on the user's Desktop/Downloads. Give recommendations on "
                f"which files can be cleaned up, archived, or compressed to save space:\n"
                f"{json.dumps(large_files[:10])}\n"
                f"Format as clear bullet points."
            )
            result = router.generate_text(prompt, task_type="general")
            return f"Storage Optimization Advice:\n{result}"
            
        elif action == "clean_temp":
            if speak:
                speak("Sir, temporary folders check and recycle bin clean kar rha hoon.")
                
            # Empty recycle bin
            # For Windows: we can run a PowerShell script to empty recycle bin
            import subprocess
            res_msg = ""
            if platform.system() == "Windows":
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command", "Clear-RecycleBin -Force -ErrorAction SilentlyContinue"],
                        capture_output=True, timeout=10
                    )
                    res_msg += "Recycle bin emptied successfully.\n"
                except Exception as e:
                    res_msg += f"Empty Recycle Bin failed: {e}\n"
                    
            # Clear temporary files
            temp_path = os.environ.get("TEMP", "")
            if temp_path and os.path.exists(temp_path):
                cleaned_count = 0
                for item in os.listdir(temp_path):
                    try:
                        p = os.path.join(temp_path, item)
                        if os.path.isfile(p):
                            os.remove(p)
                            cleaned_count += 1
                    except Exception:
                        pass
                res_msg += f"Cleared {cleaned_count} temp files from Windows Temp directory."
                
            return res_msg
            
        else:
            return f"Unknown system intel action: {action}"
            
    except Exception as e:
        return f"AI system intelligence failed: {e}"
