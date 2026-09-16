import io
import os
import time
import json
import traceback
from datetime import datetime, timedelta, date

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image

from google import genai
from google.genai import types
import gspread
from google.auth import default
from google.oauth2 import service_account
from pydantic import BaseModel
import base64
import requests

app = FastAPI(title="Reto del Hogar")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

SHEET_NAME = "Registro_Tareas_Hogar"

TASK_POINTS = {
    "Esterilizar Gata": 3000,
    "Crear y Separar Arenero para Otra Gata": 2000,
    "Planchar la ropa": 1000,
    "Desparasitar Gata": 1000,
    "Arrancar Proyecto de Ortodoncia": 500, 
    "Limpiar las cacas / arenero": 200,
    "Lavar los baños": 300,
    "Hacer la comida": 300,
    "Calentar la comida": 100, 
    "Lavar los platos": 200,
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

class MoneyRequest(BaseModel):
    user_name: str
    amount: float

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

def obtener_puntos_semana(usuario_keyword: str) -> int:
    try:
        creds = get_google_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open(SHEET_NAME).sheet1
        filas = sheet.get_all_values()
        if len(filas) <= 1:
            return 0

        now = get_colombia_now()
        hoy_date = now.date()
        inicio_semana_date = hoy_date - timedelta(days=hoy_date.weekday())
        
        total_puntos_ganados = 0
        total_puntos_redimidos = 0
        user_kw = usuario_keyword.lower()

        for fila in filas[1:]:
            if len(fila) < 6:
                continue
            fecha_str = str(fila[0]).strip()
            usuario_val = str(fila[1]).strip().lower()
            completado_val = str(fila[4]).strip().lower()
            puntos_str = str(fila[5]).strip()
            puntos_redimidos_str = str(fila[10]).strip() if len(fila) > 10 else "0"

            if completado_val not in ["sí", "si", "true", "1", "yes"]:
                continue

            matched = False
            if "val" in user_kw and "val" in usuario_val:
                matched = True
            elif ("gab" in user_kw or "gabi" in user_kw) and "gab" in usuario_val:
                matched = True
            elif ("jaiv" in user_kw) and ("jaiv" in usuario_val or "martinez" in usuario_val):
                matched = True
            elif ("eli" in user_kw or "parra" in user_kw) and ("eli" in usuario_val or "parra" in usuario_val):
                matched = True

            if not matched:
                continue

            try:
                fila_date = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
            except ValueError:
                continue

            if fila_date >= inicio_semana_date:
                try:
                    pts = int(float(puntos_str))
                    if pts > 0:
                        total_puntos_ganados += pts
                    elif pts < 0:
                        total_puntos_redimidos += abs(pts)
                except ValueError:
                    pass

                try:
                    redim = int(float(puntos_redimidos_str))
                    if redim > 0:
                        total_puntos_redimidos += redim
                except ValueError:
                    pass

        return max(0, total_puntos_ganados - total_puntos_redimidos)
    except Exception as e:
        print(f"❌ Error al obtener puntos semanales: {e}")
        return 0

@app.get("/saludo")
async def saludo():
    return {
        "proyecto": "RetoHogar",
        "mensaje": "¡Bienvenido al proyecto RetoHogar! Tu plataforma para gamificar y organizar las tareas del hogar."
    }

@app.post("/api/validate-money-request")
async def validate_money_request(req: MoneyRequest):
    try:
        user_name = req.user_name
        requested_amount = req.amount
        
        if requested_amount <= 0:
            return {
                "status": "error",
                "message": "Ingresa un monto en pesos ($) mayor a cero."
            }
            
        puntos_actuales = obtener_puntos_semana(user_name)
        puntos_a_descontar = int((requested_amount / 10000.0) * 1000.0)
        
        if puntos_a_descontar <= 0:
            return {
                "status": "error",
                "message": f"No tienes suficientes puntos acumulados esta semana (Tienes {puntos_actuales} pts disponibles, mínimo 1000 pts requeridos)."
            }
            
        if puntos_actuales < puntos_a_descontar:
            return {
                "status": "error",
                "message": f"No tienes suficientes puntos acumulados esta semana (Tienes {puntos_actuales} pts, requieres {puntos_a_descontar} pts para ${requested_amount:,.0f} COP)."
            }
            
        return {
            "status": "success",
            "message": "Solicitud viable.",
            "puntos_a_descontar": puntos_a_descontar,
            "puntos_actuales": puntos_actuales
        }
    except Exception as e:
        print(f"❌ Error validando solicitud de dinero: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/api/request-money")
async def request_money(req: MoneyRequest):
    try:
        user_name = req.user_name
        requested_amount = req.amount
        
        if requested_amount <= 0:
            return {
                "status": "error",
                "message": "Ingresa un monto en pesos ($) mayor a cero."
            }
            
        puntos_actuales = obtener_puntos_semana(user_name)
        puntos_a_descontar = int((requested_amount / 10000.0) * 1000.0)
        
        if puntos_a_descontar <= 0:
            return {
                "status": "error",
                "message": f"No tienes suficientes puntos acumulados esta semana (Tienes {puntos_actuales} pts disponibles, mínimo 1000 pts requeridos)."
            }
            
        if puntos_actuales < puntos_a_descontar:
            return {
                "status": "error",
                "message": f"No tienes suficientes puntos acumulados esta semana (Tienes {puntos_actuales} pts, requieres {puntos_a_descontar} pts para ${requested_amount:,.0f} COP)."
            }
            
        saldo_restante = puntos_actuales - puntos_a_descontar
        now_colombia = get_colombia_now().strftime("%Y-%m-%d %H:%M:%S")
        
        guardar_en_sheet([
            now_colombia,
            user_name,
            "Solicitud de Dinero Web",
            0,
            "Sí",
            0,
            0,
            f"Canje web de ${requested_amount:,.0f} COP procesado con éxito. Descuento: -{puntos_a_descontar} pts.",
            "#",
            "#",
            puntos_a_descontar
        ])
        
        return {
            "status": "success",
            "message": "¡Felicitaciones, su solicitud es viable, debe esperar a que se apruebe el desembolso del dinero",
            "puntos_descontados": puntos_a_descontar,
            "saldo_restante": saldo_restante
        }
    except Exception as e:
        print(f"❌ Error procesando solicitud de dinero: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/api/evaluate-task")
async def evaluate_task(
    user_name: str = Form(...),
    task_name: str = Form(...),
    duration_minutes: float = Form(...),
    before_photo: UploadFile = File(...),
    after_photo: UploadFile = File(...)
):
    start_time = time.time()
    req_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"📥 [{req_time_str}] Petición recibida en /api/evaluate-task | Usuario: '{user_name}' | Tarea: '{task_name}' | Duración: {duration_minutes} min")
    try:
        timestamp = int(time.time())
        
        before_bytes = await before_photo.read()
        after_bytes = await after_photo.read()
        
        before_filename = f"before_{timestamp}.jpg"
        after_filename = f"after_{timestamp}.jpg"
        
        url_foto_antes = subir_foto_drive_usuario(user_name, before_filename, before_bytes)
        url_foto_despues = subir_foto_drive_usuario(user_name, after_filename, after_bytes)
        
        img_before = Image.open(io.BytesIO(before_bytes))
        img_after = Image.open(io.BytesIO(after_bytes))
        max_score = TASK_POINTS.get(task_name, 100)
        
        prompt = (
            f"Eres el juez calificador del 'Reto del Hogar'.\n"
            f"Evalúa si la tarea '{task_name}' fue completada correctamente por '{user_name}'.\n"
            f"Compara Foto 1 (antes) con Foto 2 (después).\n"
            f"Puntaje máximo: {max_score}.\n"
            f"Si las fotos no coinciden con la tarea, completado es false y puntos es 0.\n"
            f"Devuelve strictly un JSON válido con estas llaves exactas:\n"
            f'{{"completado": true, "puntos": {max_score}, "observaciones": "evaluación detallada de 2 frases"}}'
        )

        eval_data = {"completado": True, "puntos": max_score, "observaciones": "Evaluación completada correctamente."}
        
        try:
            if client:
                primary_model = 'gemini-3.6-flash'
                fallback_model = 'gemini-2.5-flash'
                response = None
                
                print(f"🤖 [{datetime.now().strftime('%H:%M:%S')}] Intentando invocar modelo de IA principal: {primary_model}...")
                
                try:
                    response = client.models.generate_content(
                        model=primary_model,
                        contents=[img_before, img_after, prompt],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Respuesta recibida exitosamente desde {primary_model}.")
                except Exception as model_err:
                    err_type = type(model_err).__name__
                    print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] Fallo/Indisponibilidad con {primary_model} [Tipo de error: {err_type} - Detalle: {model_err}]. Transicionando al modelo de respaldo: {fallback_model}...")
                    response = client.models.generate_content(
                        model=fallback_model,
                        contents=[img_before, img_after, prompt],
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Respuesta recibida exitosamente desde el modelo de respaldo {fallback_model}.")

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
            print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] {error_msg}")
            traceback.print_exc()
            eval_data = {"completado": True, "puntos": max_score, "observaciones": error_msg}

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
            url_foto_despues,
            0
        ])

        total_duration = round(time.time() - start_time, 2)
        print(f"⏱️ [{datetime.now().strftime('%H:%M:%S')}] Procesamiento total completado en {total_duration} segundos para /api/evaluate-task.")

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
        total_duration = round(time.time() - start_time, 2)
        print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Error crítico en evaluate_task tras {total_duration}s: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.get("/api/leaderboard")
async def get_leaderboard(periodo: str = "hoy"):
    totales = {
        "Jaiver Martínez": {"puntos": 0, "tareas": 0},
        "Gabriela Martínez": {"puntos": 0, "tareas": 0},
        "Valeria Martínez": {"puntos": 0, "tareas": 0},
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
        task_val = str(fila[2]).strip().lower()
        completado_val = str(fila[4]).strip().lower()
        puntos_str = str(fila[5]).strip()

        if "solicitud de dinero" in task_val:
            continue

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
            pts = int(float(puntos_str))
            if pts < 0:
                pts = 0
        except ValueError:
            pts = 0

        if "gab" in usuario_val:
            totales["Gabriela Martínez"]["puntos"] += pts
            totales["Gabriela Martínez"]["tareas"] += 1
        elif "val" in usuario_val:
            totales["Valeria Martínez"]["puntos"] += pts
            totales["Valeria Martínez"]["tareas"] += 1
        elif "eli" in usuario_val or "parra" in usuario_val:
            totales["Elizabeth Parra"]["puntos"] += pts
            totales["Elizabeth Parra"]["tareas"] += 1
        elif any(token in usuario_val for token in ["jaiv", "haib", "hyber", "jabe", "martinez", "martínez"]):
            totales["Jaiver Martínez"]["puntos"] += pts
            totales["Jaiver Martínez"]["tareas"] += 1

    return totales

@app.get("/api/cooperative-goal")
async def get_cooperative_goal(meta_semanal: int = 12000):
    total_puntos_semana = 0
    try:
        creds = get_google_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open(SHEET_NAME).sheet1
        filas = sheet.get_all_values()
        
        if len(filas) > 1:
            now = get_colombia_now()
            hoy_date = now.date()
            inicio_semana_date = hoy_date - timedelta(days=hoy_date.weekday())
            
            for fila in filas[1:]:
                if len(fila) < 6:
                    continue
                fecha_str = str(fila[0]).strip()
                task_val = str(fila[2]).strip().lower()
                completado_val = str(fila[4]).strip().lower()
                puntos_str = str(fila[5]).strip()
                
                if "solicitud de dinero" in task_val:
                    continue

                if completado_val not in ["sí", "si", "true", "1", "yes"]:
                    continue
                try:
                    fila_date = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                
                if fila_date >= inicio_semana_date:
                    try:
                        pts = int(float(puntos_str))
                        if pts > 0:
                            total_puntos_semana += pts
                    except ValueError:
                        pass
    except Exception as e:
        print(f"Error calculando meta grupal: {e}")
        
    porcentaje = min(100, int((total_puntos_semana / meta_semanal) * 100))
    return {
        "puntos_actuales": total_puntos_semana,
        "meta": meta_semanal,
        "porcentaje": porcentaje,
        "completada": total_puntos_semana >= meta_semanal
    }

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
                
                if "solicitud de dinero" in task_name.lower():
                    continue

                observaciones = str(fila[7]).strip() if len(fila) > 7 else "Sin observaciones"
                before_url = str(fila[8]).strip() if len(fila) > 8 else "#"
                after_url = str(fila[9]).strip() if len(fila) > 9 else "#"

                if completado_val not in ["sí", "si", "true", "1", "yes"]:
                    continue

                user_lower = user.lower()
                row_user_lower = usuario_val.lower()
                matched = False
                if "gab" in user_lower and "gab" in row_user_lower:
                    matched = True
                elif "val" in user_lower and "val" in row_user_lower:
                    matched = True
                elif ("eli" in user_lower or "parra" in user_lower) and ("eli" in row_user_lower or "parra" in row_user_lower):
                    matched = True
                elif ("jaiv" in user_lower or "hyber" in user_lower or "haib" in user_lower) and ("jaiv" in row_user_lower or "hyber" in row_user_lower or "haib" in row_user_lower or "jabe" in row_user_lower or (("gab" not in row_user_lower and "val" not in row_user_lower and "eli" not in row_user_lower and "parra" not in row_user_lower) and any(t in row_user_lower for t in ["jaiv", "martinez", "martínez"]))):
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
                    if pts < 0:
                        pts = 0
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
