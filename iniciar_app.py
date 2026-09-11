import os
import time
import threading
import subprocess
import re
from google.colab import auth, userdata
import uvicorn

print("🔒 1. Autenticando con Google...")
auth.authenticate_user()

try:
    os.environ["GEMINI_API_KEY"] = userdata.get('GEMINI_API_KEY')
    print("✅ GEMINI_API_KEY cargada correctamente.")
except Exception as e:
    print(f"⚠️ AVISO Secret: {e}")

# Instalar dependencias esenciales
get_ipython().system("pip install -q fastapi uvicorn python-multipart gspread google-genai pillow")

# Verificar que app.py exista
if not os.path.exists("app.py"):
    print("❌ ERROR: No se encuentra el archivo 'app.py'. Asegúrate de ejecutar primero la celda que lo crea con %%writefile app.py.")
else:
    # 2. Levantar Uvicorn en un hilo interno en segundo plano
    def run_fastapi():
        uvicorn.run("app:app", host="127.0.0.1", port=8000, log_level="warning")

    server_thread = threading.Thread(target=run_fastapi, daemon=True)
    server_thread.start()
    print("🚀 2. Servidor FastAPI corriendo en segundo plano (puerto 8000)...")
    time.sleep(2)

    # 3. Descargar Cloudflare si no está
    if not os.path.exists("./cloudflared"):
        get_ipython().system("curl -sL --output cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64")
        get_ipython().system("chmod +x cloudflared")

    # 4. Lanzar Cloudflare y capturar su URL
    print("🌐 3. Generando túnel de Cloudflare...")
    subprocess.Popen(["./cloudflared", "tunnel", "--url", "http://127.0.0.1:8000"], stdout=open("tunnel.log", "w"), stderr=subprocess.STDOUT)

    tunnel_url = None
    for _ in range(10):
        time.sleep(1)
        if os.path.exists("tunnel.log"):
            with open("tunnel.log", "r") as f:
                content = f.read()
                match = re.search(r"https://[-0-9a-z]*\.trycloudflare\.com", content)
                if match:
                    tunnel_url = match.group(0)
                    break

    if tunnel_url:
        print(f"\n✨ ¡TODO LISTO Y FUNCIONANDO! Entra a este enlace:\n👉 {tunnel_url}\n")
    else:
        print("⚠️ Revisa el archivo 'tunnel.log' si la URL tarda en aparecer.")