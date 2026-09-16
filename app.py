import io
import os
import time
import json
import traceback
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request, Cookie, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
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

class AdminActionRequest(BaseModel):
    row_index: int
    action: str

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

def enviar_correo_smtp(asunto: str, contenido_html: str):
    try:
        smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "jaiver.martinez@gmail.com")
        smtp_password = os.environ.get("SMTP_PASSWORD", "")

        if not smtp_password:
            print("⚠️ SMTP_PASSWORD no configurado en el entorno. No se pudo enviar el correo.")
            return

        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = "jaiver.martinez@gmail.com"
        msg['Subject'] = asunto

        msg.attach(MIMEText(contenido_html, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port, timeout=5) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, "jaiver.martinez@gmail.com", msg.as_string())
        print("✅ Correo de notificación SMTP enviado exitosamente.")
    except Exception as e:
        print(f"⚠️ Aviso SMTP (Red bloqueada en Render o fallo de conexión): {e}")

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
            estado_val = str(fila[11]).strip().lower() if len(fila) > 11 else ""

            if estado_val == "rechazado":
                continue

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
        
        if puntos_actuales < puntos_a_descontar:
            return {
                "status": "error",
                "message": f"No tienes suficientes puntos acumulados esta semana."
            }
            
        now_colombia = get_colombia_now().strftime("%Y-%m-%d %H:%M:%S")
        
        guardar_en_sheet([
            now_colombia,
            user_name,
            "Solicitud de Dinero Web",
            0,
            "Sí",
            0,
            0,
            f"Solicitud web de ${requested_amount:,.0f} COP pendiente de aprobación. ({puntos_a_descontar} pts)",
            "#",
            "#",
            0,
            "Pendiente"
        ])
        
        asunto_correo = f"💰 Nueva Solicitud de Dinero Pendiente - {user_name}"
        cuerpo_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #0284c7;">Nueva Solicitud de Dinero en Reto del Hogar</h2>
            <p>Se ha registrado una nueva solicitud que requiere tu aprobación:</p>
            <ul>
                <li><strong>Solicitante:</strong> {user_name}</li>
                <li><strong>Monto:</strong> ${requested_amount:,.0f} COP</li>
                <li><strong>Puntos equivalentes:</strong> {puntos_a_descontar} pts</li>
                <li><strong>Fecha:</strong> {now_colombia}</li>
            </ul>
            <p>Por favor ingresa al panel de administración para aprobar o rechazar la solicitud.</p>
        </body>
        </html>
        """
        enviar_correo_smtp(asunto_correo, cuerpo_html)
        
        return {
            "status": "success",
            "message": "¡Felicitaciones, su solicitud es viable, debe esperar a que se apruebe el desembolso del dinero",
            "puntos_descontados": 0,
            "saldo_restante": puntos_actuales
        }
    except Exception as e:
        print(f"❌ Error procesando solicitud de dinero: {e}")
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.get("/admin/login")
async def admin_login_get(response: Response):
    resp = RedirectResponse(url="/admin/solicitudes", status_code=303)
    resp.set_cookie(key="admin_user", value="Jaiver Martínez", httponly=True)
    return resp

@app.get("/admin/solicitudes", response_class=HTMLResponse)
async def admin_solicitudes_view(request: Request, admin_user: str = Cookie(None)):
    if not admin_user or "jaiv" not in admin_user.lower():
        return HTMLResponse("<h3>Acceso denegado. Este panel es exclusivo para el administrador Jaiver Martínez.</h3><p><a href='/admin/login'>Iniciar sesión como Administrador</a></p>", status_code=403)

    try:
        creds = get_google_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open(SHEET_NAME).sheet1
        filas = sheet.get_all_values()
        
        solicitudes_pendientes = []
        if len(filas) > 1:
            for idx, fila in enumerate(filas[1:], start=2):
                if len(fila) >= 12 and "solicitud de dinero" in str(fila[2]).lower() and str(fila[11]).strip().lower() == "pendiente":
                    solicitudes_pendientes.append({
                        "row_index": idx,
                        "fecha": fila[0],
                        "usuario": fila[1],
                        "detalle": fila[7],
                        "puntos": fila[10] if len(fila) > 10 else "0"
                    })
    except Exception as e:
        print(f"Error cargando solicitudes pendientes: {e}")
        solicitudes_pendientes = []

    html_content = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Panel de Administración - Reto del Hogar</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen p-6">
        <div class="max-w-4xl mx-auto space-y-6">
            <div class="flex justify-between items-center border-b border-slate-700 pb-4">
                <h1 class="text-2xl font-bold text-amber-400">🛡️ Panel de Administración - Aprobación de Solicitudes</h1>
                <a href="/" class="bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded-lg text-xs">Volver al Inicio</a>
            </div>
            
            <div class="bg-slate-800 rounded-2xl p-5 border border-slate-700 shadow-xl space-y-4">
                <h2 class="text-lg font-bold text-cyan-400">Solitudes de Dinero Pendientes</h2>
    """

    if not solicitudes_pendientes:
        html_content += '<p class="text-slate-400 text-sm py-4">No hay solicitudes pendientes de aprobación en este momento.</p>'
    else:
        html_content += '<div class="space-y-3">'
        for sol in solicitudes_pendientes:
            html_content += f"""
                <div class="bg-slate-900 border border-slate-700 rounded-xl p-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                    <div>
                        <div class="font-bold text-amber-300 text-base">{sol['usuario']}</div>
                        <div class="text-xs text-slate-300">{sol['detalle']}</div>
                        <div class="text-[10px] text-slate-500 mt-1">📅 {sol['fecha']}</div>
                    </div>
                    <div class="flex gap-2 w-full sm:w-auto">
                        <button onclick="procesarSolicitud({sol['row_index']}, 'aprobar')" class="flex-1 sm:flex-none bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-4 py-2 rounded-lg text-xs transition">Aprobar</button>
                        <button onclick="procesarSolicitud({sol['row_index']}, 'rechazar')" class="flex-1 sm:flex-none bg-red-600 hover:bg-red-500 text-white font-bold px-4 py-2 rounded-lg text-xs transition">Rechazar</button>
                    </div>
                </div>
            """
        html_content += '</div>'

    html_content += f"""
            </div>
        </div>
        <script>
            async function procesarSolicitud(rowIndex, action) {{
                if(!confirm(`¿Estás seguro de que deseas ${{action}} esta solicitud?`)) return;
                try {{
                    const res = await fetch('/api/admin/process-money', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ row_index: rowIndex, action: action }})
                    }});
                    const data = await res.json();
                    if(data.status === 'success') {{
                        alert(data.message);
                        location.reload();
                    }} else {{
                        alert('Error: ' + data.message);
                    }}
                }} catch(e) {{
                    alert('Error de conexión al procesar la solicitud.');
                }}
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html_content)

@app.post("/api/admin/process-money")
async def process_money_request(req: AdminActionRequest, admin_user: str = Cookie(None)):
    if not admin_user or "jaiv" not in admin_user.lower():
        raise HTTPException(status_code=403, detail="No autorizado")

    try:
        creds = get_google_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open(SHEET_NAME).sheet1
        
        row_idx = req.row_index
        action = req.action.lower()
        
        fila = sheet.row_values(row_idx)
        if not fila or len(fila) < 12:
            return {"status": "error", "message": "Fila no encontrada o inválida en la hoja."}

        detalle_actual = fila[7]
        
        if action == "aprobar":
            puntos_a_descontar = 1000
            try:
                import re
                match = re.search(r'\((\d+)\s*pts\)', detalle_actual)
                if match:
                    puntos_a_descontar = int(match.group(1))
            except:
                pass

            sheet.update_cell(row_idx, 11, puntos_a_descontar)
            sheet.update_cell(row_idx, 12, "Aprobado")
            sheet.update_cell(row_idx, 8, detalle_actual.replace("pendiente de aprobación", "APROBADO por administrador"))
            
            return {"status": "success", "message": "Solicitud aprobada con éxito. Puntos descontados."}
            
        elif action == "rechazar":
            sheet.update_cell(row_idx, 12, "Rechazado")
            sheet.update_cell(row_idx, 8, detalle_actual.replace("pendiente de aprobación", "RECHAZADO por administrador"))
            
            return {"status": "success", "message": "Solicitud rechazada. Los puntos se mantienen intactos."}
        else:
            return {"status": "error", "message": "Acción no reconocida."}

    except Exception as e:
        print(f"❌ Error en process_money_request: {e}")
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
                response = None
                
                print(f"🤖 [{datetime.now().strftime('%H:%M:%S')}] Intentando invocar modelo de IA principal: {primary_model}...")
                
                max_retries = 3
                for attempt in range(1, max_retries + 1):
                    try:
                        response = client.models.generate_content(
                            model=primary_model,
                            contents=[img_before, img_after, prompt],
                            config=types.GenerateContentConfig(response_mime_type="application/json")
                        )
                        print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Respuesta recibida exitosamente desde {primary_model}.")
                        break
                    except Exception as model_err:
                        print(f"⚠️ Intento {attempt} falló con {primary_model}: {model_err}")
                        if attempt == max_retries:
                            raise model_err
                        time.sleep(2)

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
            err_str = str(e).lower()
            if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
                error_msg = "En este momento la evaluación de la IA no está disponible por falta de cuota. Estará disponible al día siguiente."
            else:
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
            0,
            "Completado"
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
        estado_val = str(fila[11]).strip().lower() if len(fila) > 11 else ""

        if estado_val == "rechazado":
            continue

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
                estado_val = str(fila[11]).strip().lower() if len(fila) > 11 else ""
                
                if estado_val == "rechazado":
                    continue

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
                estado_val = str(fila[11]).strip().lower() if len(fila) > 11 else ""
                
                if estado_val == "rechazado":
                    continue
                
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