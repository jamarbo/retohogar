import io
import os
import time
import json
import traceback
from datetime import datetime, timedelta, date

from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import FileResponse
from PIL import Image

from google import genai
from google.genai import types
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

app = FastAPI(title="Reto del Hogar")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SPREADSHEET_ID = "183uPElazqaz9QO9vdrj-9W3XcXKiOLFCRVzSiAMLaliQ"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_credentials():
    cred_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if cred_json:
        try:
            info = json.loads(cred_json)
            return Credentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as e:
            print(f"❌ Error leyendo GOOGLE_CREDENTIALS_JSON: {e}")
            traceback.print_exc()
    if os.path.exists("credentials.json"):
        return Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    from google.auth import default
    creds, _ = default(scopes=SCOPES)
    return creds

TASK_POINTS = {
    "Limpiar las cacas / arenero": 200,
    "Trapear los baños": 180,
    "Hacer la comida": 150,
    "Lavar los platos": 150,
    "Doblar la ropa dentro de los clósets": 140,
    "Sacar la ropa de la lavadora": 120,
    "Echar ropa a la lavadora": 110,
    "Botar la basura": 100,
    "Lavar la nevera": 90,
    "Trapear la sala": 80,
    "Trapear las habitaciones": 80,
    "Colgar la ropa a secar": 70,
    "Barrer la sala": 60,
    "Barrer las habitaciones": 60,
    "Limpiar los espejos": 50,
    "Limpiar el polvo de muebles": 50,
    "Tender la cama": 40
}

active_sessions = {}

def get_colombia_now():
    return datetime.utcnow() - timedelta(hours=5)

def extraer_puntos_y_datos(fila):
    puntos = 0
    completado = False
    
    for val in fila:
        val_str = str(val).strip().lower()
        if val_str in ["sí", "si", "true", "1", "yes"]:
            completado = True
            
    for val in fila:
        val_str = str(val).strip().replace(",", ".")
        try:
            num = float(val_str)
            if num.is_integer() and 10 <= num <= 500:
                puntos = int(num)
                break
        except ValueError:
            continue
            
    return completado, puntos

def subir_foto_drive_usuario(user_name, filename, photo_bytes):
    try:
        creds = get_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        query_root = "mimeType='application/vnd.google-apps.folder' and name='Evidencias_Tareas_Hogar' and trashed=false"
        res = drive_service.files().list(q=query_root, spaces='drive', fields='files(id)').execute()
        files = res.get('files', [])
        root_id = files[0]['id'] if files else drive_service.files().create(body={'name': 'Evidencias_Tareas_Hogar', 'mimeType': 'application/vnd.google-apps.folder'}, fields='id').execute().get('id')

        file_metadata = {'name': filename, 'parents': [root_id]}
        media = MediaIoBaseUpload(io.BytesIO(photo_bytes), mimetype='image/jpeg', resumable=True)
        file_obj = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()

        file_id = file_obj.get('id')
        try:
            drive_service.permissions().create(fileId=file_id, body={'type': 'anyone', 'role': 'reader'}).execute()
        except Exception:
            pass

        return file_obj.get('webViewLink') or f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as e:
        print(f"⚠️ Aviso subiendo a Drive: {e}")
        return "#"

def guardar_en_sheet(fila):
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SPREADSHEET_ID)
        sh.sheet1.append_row(fila)
        print("✅ Registro guardado en Sheets con éxito.")
    except Exception as e:
        print(f"❌ Error al guardar en Sheets: {e}")
        traceback.print_exc()

@app.post("/api/start-task")
async def start_task(user_name: str = Form(...), task_name: str = Form(...), before_photo: UploadFile = File(...)):
    try:
        timestamp = int(time.time())
        session_id = f"{user_name}_{task_name}_{timestamp}"
        photo_bytes = await before_photo.read()
        drive_url = subir_foto_drive_usuario(user_name, f"before_{timestamp}.jpg", photo_bytes)
        active_sessions[session_id] = {
            "user_name": user_name, "task_name": task_name,
            "start_time": time.time(), "before_photo": photo_bytes, "before_url": drive_url
        }
        return {"status": "started", "session_id": session_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/finish-task")
async def finish_task(session_id: str = Form(...), after_photo: UploadFile = File(...)):
    try:
        if session_id not in active_sessions:
            return {"status": "error", "message": "Sesión no encontrada."}
        session = active_sessions[session_id]
        duration_minutes = round((time.time() - session["start_time"]) / 60, 2)
        after_bytes = await after_photo.read()
        after_drive_url = subir_foto_drive_usuario(session['user_name'], f"after_{int(time.time())}.jpg", after_bytes)

        img_before = Image.open(io.BytesIO(session["before_photo"]))
        img_after = Image.open(io.BytesIO(after_bytes))
        max_score = TASK_POINTS.get(session['task_name'], 100)

        prompt = (
            f"Evalúa si la tarea '{session['task_name']}' fue completada correctamente. "
            f"Puntaje máximo: {max_score}. "
            f"Devuelve estrictamente un JSON válido: {{\"completado\": true, \"puntos\": {max_score}, \"observaciones\": \"buen trabajo\"}}"
        )

        try:
            if client:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[img_before, img_after, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                raw = response.text.strip()
                if raw.startswith("```json"): raw = raw[7:-3].strip()
                elif raw.startswith("```"): raw = raw[3:-3].strip()
                eval_data = json.loads(raw)
            else:
                eval_data = {"completado": True, "puntos": max_score, "observaciones": "Completado."}
        except Exception:
            eval_data = {"completado": True, "puntos": max_score, "observaciones": "Registrado con éxito."}

        now_colombia = get_colombia_now().strftime("%Y-%m-%d %H:%M:%S")
        guardar_en_sheet([
            now_colombia, session['user_name'], session['task_name'], duration_minutes,
            "Sí" if eval_data.get('completado') else "No", eval_data.get('puntos', 0),
            max_score, eval_data.get('observaciones', ''), session['before_url'], after_drive_url
        ])

        url_before = session['before_url']
        del active_sessions[session_id]
        return {
            "status": "finished", "user_name": session['user_name'], "duration_minutes": duration_minutes,
            "max_points": max_score, "completado": eval_data.get('completado', False),
            "puntos": eval_data.get('puntos', 0), "observaciones": eval_data.get('observaciones', ''),
            "before_url": url_before, "after_url": after_drive_url
        }
    except Exception as e:
        return {"status": "finished", "user_name": "Usuario", "duration_minutes": 1, "max_points": 100, "completado": True, "puntos": 50, "observaciones": "Registrado.", "before_url": "#", "after_url": "#"}

@app.get("/api/leaderboard")
async def get_leaderboard(periodo: str = "hoy"):
    totales = {
        "Jaiver Martínez": {"puntos": 0, "tareas": 0},
        "Gabriela": {"puntos": 0, "tareas": 0},
        "Valeria": {"puntos": 0, "tareas": 0},
        "Elizabeth Parra": {"puntos": 0, "tareas": 0}
    }
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
        filas = sheet.get_all_values()
    except Exception as e:
        print("❌ Error detallado en Sheets (/api/leaderboard):")
        traceback.print_exc()
        return totales

    if len(filas) <= 1: return totales

    now = get_colombia_now()
    hoy_date = now.date()
    inicio_semana_date = hoy_date - timedelta(days=hoy_date.weekday())
    inicio_mes_date = date(hoy_date.year, hoy_date.month, 1)

    for fila in filas[1:]:
        if len(fila) < 2: continue
        fecha_str = str(fila[0]).strip()
        usuario_val = str(fila[1]).strip().lower()

        completado, pts = extraer_puntos_y_datos(fila)
        if not completado: continue

        try:
            fila_date = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
        except ValueError:
            try: fila_date = datetime.strptime(fecha_str[:10], "%y-%m-%d").date()
            except ValueError: continue

        if periodo == "hoy" and fila_date != hoy_date: continue
        elif periodo == "semana" and fila_date < inicio_semana_date: continue
        elif periodo == "mes" and fila_date < inicio_mes_date: continue

        if any(t in usuario_val for t in ["jaiv", "haib", "jabe", "martinez", "martínez"]):
            totales["Jaiver Martínez"]["puntos"] += pts
            totales["Jaiver Martínez"]["tareas"] += 1
        elif "gab" in usuario_val:
            totales["Gabriela"]["puntos"] += pts
            totales["Gabriela"]["tareas"] += 1
        elif "val" in usuario_val:
            totales["Valeria"]["puntos"] += pts
            totales["Valeria"]["tareas"] += 1
        elif "eli" in usuario_val or "parra" in usuario_val:
            totales["Elizabeth Parra"]["puntos"] += pts
            totales["Elizabeth Parra"]["tareas"] += 1
    return totales

@app.get("/api/user-tasks")
async def get_user_tasks(user: str, periodo: str = "semana"):
    user_tasks = []
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
        filas = sheet.get_all_values()
        if len(filas) <= 1: return user_tasks

        now = get_colombia_now()
        hoy_date = now.date()
        inicio_semana_date = hoy_date - timedelta(days=hoy_date.weekday())
        inicio_mes_date = date(hoy_date.year, hoy_date.month, 1)

        for fila in filas[1:]:
            if len(fila) < 2: continue
            fecha_str = str(fila[0]).strip()
            usuario_val = str(fila[1]).strip()
            task_name = str(fila[2]).strip() if len(fila) > 2 else "Tarea"
            duracion = str(fila[3]).strip() if len(fila) > 3 else "0"

            completado, pts = extraer_puntos_y_datos(fila)
            if not completado: continue

            user_lower = user.lower()
            row_user_lower = usuario_val.lower()
            matched = any(t in row_user_lower for t in ["jaiv", "martinez"]) if "jaiv" in user_lower else \
                      ("gab" in user_lower and "gab" in row_user_lower) or \
                      ("val" in user_lower and "val" in row_user_lower) or \
                      ("eli" in user_lower and ("eli" in row_user_lower or "parra" in row_user_lower))
            if not matched: continue

            try: fila_date = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
            except ValueError:
                try: fila_date = datetime.strptime(fecha_str[:10], "%y-%m-%d").date()
                except ValueError: continue

            if periodo == "hoy" and fila_date != hoy_date: continue
            elif periodo == "semana" and fila_date < inicio_semana_date: continue
            elif periodo == "mes" and fila_date < inicio_mes_date: continue

            obs = str(fila[7]).strip() if len(fila) > 7 else "Sin observaciones"
            b_url = next((str(v).strip() for v in fila if str(v).strip().startswith("http")), "#")
            a_url = b_url

            user_tasks.append({
                "fecha": fecha_str, "task_name": task_name, "duracion": duracion,
                "puntos": pts, "observaciones": obs, "before_url": b_url, "after_url": a_url
            })
    except Exception as e:
        traceback.print_exc()
    return user_tasks

@app.get("/")
async def home():
    return FileResponse("index.html")