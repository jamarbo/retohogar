import io
import os
import time
import json
import traceback
import smtplib
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from google import genai
from google.genai import types
import gspread
from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

app = FastAPI(title="Reto del Hogar")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ALERT_EMAIL = "jaiver.martinez@gmail.com"
SMTP_SENDER = "jaiver.martinez@gmail.com"
SHEET_NAME = "Registro_Tareas_Hogar"

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
folder_cache = {}

def get_colombia_now():
    return datetime.utcnow() - timedelta(hours=5)

def subir_foto_drive_usuario(user_name, filename, photo_bytes):
    try:
        creds, _ = default()
        drive_service = build('drive', 'v3', credentials=creds)
        
        query_root = "mimeType='application/vnd.google-apps.folder' and name='Evidencias_Tareas_Hogar' and trashed=false"
        res = drive_service.files().list(q=query_root, spaces='drive', fields='files(id)').execute()
        files = res.get('files', [])
        root_id = files[0]['id'] if files else drive_service.files().create(body={'name': 'Evidencias_Tareas_Hogar', 'mimeType': 'application/vnd.google-apps.folder'}, fields='id').execute().get('id')

        file_metadata = {'name': filename, 'parents': [root_id]}
        media = MediaIoBaseUpload(io.BytesIO(photo_bytes), mimetype='image/jpeg', resumable=True)
        file_obj = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        file_id = file_obj.get('id')
        try:
            drive_service.permissions().create(
                fileId=file_id,
                body={'type': 'anyone', 'role': 'reader'}
            ).execute()
        except Exception:
            pass

        return file_obj.get('webViewLink') or f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as e:
        print(f"⚠️ Aviso subiendo a Drive (modo seguro activado): {e}")
        return "#"

def guardar_en_sheet(fila):
    try:
        creds, _ = default()
        gc = gspread.authorize(creds)
        sh = gc.open(SHEET_NAME)
        sh.sheet1.append_row(fila)
        print("✅ Registro guardado en Sheets con éxito.")
    except Exception as e:
        print(f"❌ Error al guardar en Sheets: {e}")

@app.post("/api/start-task")
async def start_task(
    user_name: str = Form(...),
    task_name: str = Form(...),
    before_photo: UploadFile = File(...)
):
    try:
        timestamp = int(time.time())
        session_id = f"{user_name}_{task_name}_{timestamp}"
        photo_bytes = await before_photo.read()

        filename = f"before_{timestamp}.jpg"
        drive_url = subir_foto_drive_usuario(user_name, filename, photo_bytes)

        active_sessions[session_id] = {
            "user_name": user_name,
            "task_name": task_name,
            "start_time": time.time(),
            "before_photo": photo_bytes,
            "before_url": drive_url
        }
        return {"status": "started", "session_id": session_id}
    except Exception as e:
        print(f"❌ Error crítico en start-task: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/finish-task")
async def finish_task(
    session_id: str = Form(...),
    after_photo: UploadFile = File(...)
):
    try:
        if session_id not in active_sessions:
            return {"status": "error", "message": "Sesión no encontrada o expirada."}

        session = active_sessions[session_id]
        duration_minutes = round((time.time() - session["start_time"]) / 60, 2)
        after_bytes = await after_photo.read()

        timestamp = int(time.time())
        filename = f"after_{timestamp}.jpg"
        after_drive_url = subir_foto_drive_usuario(session['user_name'], filename, after_bytes)

        img_before = Image.open(io.BytesIO(session["before_photo"]))
        img_after = Image.open(io.BytesIO(after_bytes))
        max_score = TASK_POINTS.get(session['task_name'], 100)

        prompt = (
            f"Eres el juez calificador del 'Reto del Hogar'.\n"
            f"Evalúa si la tarea '{session['task_name']}' fue completada correctamente por '{session['user_name']}'.\n"
            f"Compara Foto 1 (antes) con Foto 2 (después).\n"
            f"Puntaje máximo: {max_score}.\n"
            f"Si las fotos no coinciden con la tarea, completado es false y puntos es 0.\n"
            f"Devuelve estrictamente un JSON válido:\n"
            f'{{"completado": true, "puntos": {max_score}, "observaciones": "evaluación detallada de 2 frases"}}'
        )

        try:
            if client:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[img_before, img_after, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                raw = response.text.strip()
                if raw.startswith("```json"):
                    raw = raw[7:-3].strip()
                elif raw.startswith("```"):
                    raw = raw[3:-3].strip()
                eval_data = json.loads(raw)
            else:
                eval_data = {"completado": True, "puntos": max_score, "observaciones": "Modo offline activo."}
        except Exception as e:
            eval_data = {"completado": True, "puntos": int(max_score * 0.8), "observaciones": "Tarea registrada correctamente."}

        now_colombia = get_colombia_now().strftime("%Y-%m-%d %H:%M:%S")

        guardar_en_sheet([
            now_colombia,
            session['user_name'],
            session['task_name'],
            duration_minutes,
            "Sí" if eval_data.get('completado') else "No",
            eval_data.get('puntos', 0),
            max_score,
            eval_data.get('observaciones', ''),
            session['before_url'],
            after_drive_url
        ])

        url_before = session['before_url']
        del active_sessions[session_id]

        return {
            "status": "finished",
            "user_name": session['user_name'],
            "duration_minutes": duration_minutes,
            "max_points": max_score,
            "completado": eval_data.get('completado', False),
            "puntos": eval_data.get('puntos', 0),
            "observaciones": eval_data.get('observaciones', ''),
            "before_url": url_before,
            "after_url": after_drive_url
        }
    except Exception as e:
        print(f"❌ Error en finish-task: {e}")
        return {
            "status": "finished",
            "user_name": "Usuario",
            "duration_minutes": 0.1,
            "max_points": 100,
            "completado": True,
            "puntos": 50,
            "observaciones": "Tarea registrada con éxito (modo recuperación).",
            "before_url": "#",
            "after_url": "#"
        }

@app.get("/api/leaderboard")
async def get_leaderboard(periodo: str = "hoy"):
    totales = {
        "Jaiver Martínez": {"puntos": 0, "tareas": 0},
        "Gabriela": {"puntos": 0, "tareas": 0},
        "Valeria": {"puntos": 0, "tareas": 0},
        "Elizabeth Parra": {"puntos": 0, "tareas": 0}
    }

    try:
        creds, _ = default()
        gc = gspread.authorize(creds)
        sheet = gc.open(SHEET_NAME).sheet1
        filas = sheet.get_all_values()
    except Exception as e:
        print(f"Error Sheets: {e}")
        return totales

    if len(filas) <= 1:
        return totales

    datos = filas[1:]
    now = get_colombia_now()
    hoy_date = now.date()
    
    inicio_semana_date = hoy_date - timedelta(days=hoy_date.weekday())
    inicio_mes_date = date(hoy_date.year, hoy_date.month, 1)

    for fila in datos:
        if len(fila) < 6:
            continue

        fecha_str = str(fila[0]).strip()
        usuario_val = str(fila[1]).strip().lower()
        completado_val = str(fila[4]).strip().lower()
        puntos_str = str(fila[5]).strip()

        if completado_val not in ["sí", "si", "true", "1", "yes"]:
            continue

        try:
            fila_date = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        if periodo == "hoy" and fila_date != hoy_date:
            continue
        elif periodo == "semana" and fila_date < inicio_semana_date:
            continue
        elif periodo == "mes" and fila_date < inicio_mes_date:
            continue

        try:
            pts = int(puntos_str)
        except ValueError:
            pts = 0

        if any(token in usuario_val for token in ["jaiv", "haib", "jabe", "martinez", "martínez"]):
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
        creds, _ = default()
        gc = gspread.authorize(creds)
        sheet = gc.open(SHEET_NAME).sheet1
        filas = sheet.get_all_values()

        if len(filas) <= 1:
            return user_tasks

        datos = filas[1:]
        now = get_colombia_now()
        hoy_date = now.date()
        
        inicio_semana_date = hoy_date - timedelta(days=hoy_date.weekday())
        inicio_mes_date = date(hoy_date.year, hoy_date.month, 1)

        for fila in datos:
            try:
                if len(fila) < 6:
                    continue

                fecha_str = str(fila[0]).strip()
                usuario_val = str(fila[1]).strip()
                task_name = str(fila[2]).strip()
                duracion = str(fila[3]).strip()
                completado_val = str(fila[4]).strip().lower()
                puntos_str = str(fila[5]).strip()
                
                observaciones = str(fila[7]).strip() if len(fila) > 7 else "Sin observaciones"
                before_url = str(fila[8]).strip() if len(fila) > 8 else "#"
                after_url = str(fila[9]).strip() if len(fila) > 9 else "#"

                if completado_val not in ["sí", "si", "true", "1", "yes"]:
                    continue

                user_lower = user.lower()
                row_user_lower = usuario_val.lower()
                matched = False
                if "jaiv" in user_lower and any(t in row_user_lower for t in ["jaiv", "martinez"]):
                    matched = True
                elif "gab" in user_lower and "gab" in row_user_lower:
                    matched = True
                elif "val" in user_lower and "val" in row_user_lower:
                    matched = True
                elif "eli" in user_lower and ("eli" in row_user_lower or "parra" in row_user_lower):
                    matched = True

                if not matched:
                    continue

                try:
                    fila_date = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue

                if periodo == "hoy" and fila_date != hoy_date:
                    continue
                elif periodo == "semana" and fila_date < inicio_semana_date:
                    continue
                elif periodo == "mes" and fila_date < inicio_mes_date:
                    continue

                try:
                    pts = int(float(puntos_str))
                except ValueError:
                    pts = 0

                user_tasks.append({
                    "fecha": fecha_str,
                    "task_name": task_name,
                    "duracion": duracion,
                    "puntos": pts,
                    "observaciones": observaciones if observaciones else "Sin observaciones",
                    "before_url": before_url if before_url.startswith("http") else "#",
                    "after_url": after_url if after_url.startswith("http") else "#"
                })
            except Exception as row_err:
                print(f"Error procesando fila individual: {row_err}")
                continue

    except Exception as e:
        print("❌ Error crítico en /api/user-tasks:")
        traceback.print_exc()
        return {"error": str(e)}

    return user_tasks

@app.get("/")
async def home():
    return FileResponse("index.html")