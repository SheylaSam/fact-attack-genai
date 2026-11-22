"""Build React and serve the complete app on one local port using this Python environment."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

TRACK = Path(__file__).resolve().parents[1]
FRONTEND = TRACK.parent / 'frontend'


def main():
    missing = [name for name in ('fastapi', 'uvicorn', 'httpx', 'langgraph', 'langchain_openai', 'multipart')
               if importlib.util.find_spec(name) is None]
    if missing:
        raise SystemExit('Fehlende Web-Abhängigkeiten: ' + ', '.join(missing) + '\nBitte zuerst make setup-web ausführen.')
    npm = shutil.which('npm')
    if npm is None:
        raise SystemExit('Node.js mit npm fehlt. Bitte Node.js 22.12+ installieren und make web erneut ausführen.')
    if not (FRONTEND / 'node_modules').is_dir():
        subprocess.run([npm, 'ci'], cwd=FRONTEND, check=True)
    # Always type-check and build: the browser never receives stale UI code.
    subprocess.run([npm, 'run', 'build'], cwd=FRONTEND, check=True)
    os.chdir(TRACK)
    print('\nFact Attack starten: http://127.0.0.1:8000\nBeenden mit Ctrl+C.\n', flush=True)
    os.execv(sys.executable, [sys.executable, '-m', 'uvicorn', 'src.web.api:app',
                            '--host', '127.0.0.1', '--port', '8000'])


if __name__ == '__main__':
    main()
