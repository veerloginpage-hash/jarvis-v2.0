import subprocess
import sys

print("Installing requirements...")
# llama-cpp-python publishes a prebuilt Windows CPU wheel on its official
# wheel index; the source package needs a C++ compiler that most users do not
# have installed.  Keep the normal requirements flow, but prefer that wheel.
subprocess.run([
    sys.executable, "-m", "pip", "install", "-r", "requirements.txt",
    "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cpu",
], check=True)

print("Installing Playwright browsers...")
subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)

print("\n✅ Setup complete! Run 'python main.py' to start VEER INDUS.")

