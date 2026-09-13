import io
import os
import time
import json
import traceback
from datetime import datetime, timedelta, date

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from google import genai
from google.genai import types
import gspread
from google.auth import default
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

app = FastAPI(title="Reto del Hogar")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

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
    "Hacer Mandados Tienda o Droguería": 90,
    "Tirar la Basura al Shut de Basuras": 90,
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

def get_colombia_now():
    return datetime.utcnow() - timedelta(hours=5)

def get_google_credentials():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    if creds_json:
        creds_dict = json.loads(creds_json)
        return service_account.Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds, _ = default()
        return creds
        
import base64
import requests

def subir_foto_drive_usuario(user_name, filename, photo_bytes):
    try:
        github_token = os.environ.get("GITHUB_TOKEN")
        repo_name = "jamarbo/retohogar"
        
        if not github_token:
            print("⚠️ Falta configurar GITHUB_TOKEN en Render")
            return "#"
            
        encoded_content = base64.b64encode(photo_bytes).decode("utf-8")
        url = f"https://api.github.com/repos/{repo_name}/contents/evidencias/{filename}"
        
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }
        data = {
            "message": f"Evidencia automática {filename}",
            "content": encoded_content,
            "branch": "main"
        }
        
        response = requests.put(url, headers=headers, json=data)
        if response.status_code in [201, 200]:
            return f"https://raw.githubusercontent.com/{repo_name}/main/evidencias/{filename}"
        else:
            print(f"❌ Error subiendo a GitHub: {response.text}")
            return "#"
    except Exception as e:
        print(f"❌ Excepción subiendo a GitHub: {e}")
        return "#"
        
def guardar_en_sheet(fila):
    try:
        creds = get_google_credentials()
        gc = gspread.authorize(creds)
        sh = gc.open(SHEET_NAME)
        sh.sheet1.append_row(fila)
        print("✅ Registro guardado en Sheets con éxito.")
    except Exception as e:
        print(f"❌ Error al guardar en Sheets: {e}")

@app.post("/api/evaluate-task")
async def evaluate_task(
    user_name: str = Form(...),
    task_name: str = Form(...),
    duration_minutes: float = Form(...),
    before_photo: UploadFile = File(...),
    after_photo: UploadFile = File(...)
):
    try:
        timestamp = int(time.time())
        
        # 1. Leemos los bytes de ambas imágenes
        before_bytes = await before_photo.read()
        after_bytes = await after_photo.read()
        
        # 2. Subimos las fotos a GitHub (tu repositorio)
        before_filename = f"before_{timestamp}.jpg"
        after_filename = f"after_{timestamp}.jpg"
        
        url_foto_antes = subir_foto_drive_usuario(user_name, before_filename, before_bytes)
        url_foto_despues = subir_foto_drive_usuario(user_name, after_filename, after_bytes)
        
        # 3. Preparamos la llamada a Gemini
        img_before = Image.open(io.BytesIO(before_bytes))
        img_after = Image.open(io.BytesIO(after_bytes))
        max_score = TASK_POINTS.get(task_name, 100)
        
        prompt = (
            f"Eres el juez calificador del 'Reto del Hogar'.\n"
            f"Evalúa si la tarea '{task_name}' fue completada correctamente por '{user_name}'.\n"
            f"Compara Foto 1 (antes) con Foto 2 (después).\n"
            f"Puntaje máximo: {max_score}.\n"
            f"Si las fotos no coinciden con la tarea, completado es false y puntos es 0.\n"
            f"Devuelve estrictamente un JSON válido con estas llaves exactas:\n"
            f'{{"completado": true, "puntos": {max_score}, "observaciones": "evaluación detallada de 2 frases"}}'
        )

        eval_data = {"completado": True, "puntos": max_score, "observaciones": "Evaluación completada correctamente."}
        
        try:
            if client:
                response = client.models.generate_content(
                    model='gemini-1.5-flash',
                    contents=[img_before, img_after, prompt],
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                raw = response.text.strip()
                if raw.startswith("```json"):
                    raw = raw[7:-3].strip()
                elif raw.startswith("```"):
                    raw = raw[3:-3].strip()
                parsed = json.loads(raw)
                
                eval_data["completado"] = bool(parsed.get("completado", True))
                eval_data["puntos"] = int(parsed.get("puntos", max_score))
                eval_data["observaciones"] = str(parsed.get("observaciones") or parsed.get("observacion") or "Sin observaciones detalladas.")
        except Exception as e:
            error_msg = f"Error evaluando con IA: {str(e)}"
            print(f"❌ {error_msg}")
            traceback.print_exc()
            eval_data = {"completado": True, "puntos": max_score, "observaciones": error_msg}

        # 4. Guardamos en Sheets
        now_colombia = get_colombia_now().strftime("%Y-%m-%d %H:%M:%S")
        guardar_en_sheet([
            now_colombia,
            user_name,
            task_name,
            duration_minutes,
            "Sí" if eval_data.get('completado') else "No",
            eval_data.get('puntos', 0),
            max_score,
            eval_data.get('observaciones', ''),
            url_foto_antes,
            url_foto_despues
        ])

        # 5. Retornamos la respuesta al cliente
        return {
            "status": "success",
            "user_name": user_name,
            "duration_minutes": duration_minutes,
            "max_points": max_score,
            "completado": eval_data.get('completado', False),
            "puntos": eval_data.get('puntos', 0),
            "observaciones": eval_data.get('observaciones', ''),
            "before_url": url_foto_antes,
            "after_url": url_foto_despues
        }

    except Exception as e:
        print(f"❌ Error en evaluate_task: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.get("/api/leaderboard")
async def get_leaderboard(periodo: str = "hoy"):
    totales = {
        "Jaiver Martínez": {"puntos": 0, "tareas": 0},
        "Gabriela": {"puntos": 0, "tareas": 0},
        "Valeria": {"puntos": 0, "tareas": 0},
        "Elizabeth Parra": {"puntos": 0, "tareas": 0}
    }
    try:
        creds = get_google_credentials()
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
        creds = get_google_credentials()
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