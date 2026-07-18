import os
import time
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.services import (
    get_company_data, get_brand_data, search_google, 
    generate_profile_and_recommendations, evaluate_and_improve_response
)

app = FastAPI(title="INN Client Profile Service")

# Метрики Prometheus
REQUEST_COUNT = Counter('inn_service_requests_total', 'Total requests', ['status', 'corrected'])
REQUEST_LATENCY = Histogram('inn_service_latency_seconds', 'Request latency')

# Инициализация шаблонов (путь относительно корневой папки проекта)
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/metrics")
def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.post("/api/process_inn")
async def process_inn(request: Request):
    start_time = time.time()
    body = await request.json()
    inn = body.get("inn", "").strip()
    
    if not inn:
        REQUEST_COUNT.labels(status="error", corrected="false").inc()
        return JSONResponse({"error": "ИНН не указан"}, status_code=400)

    try:
        company_data, used_inn, corrected, status_message = get_company_data(inn)
        
        if status_message:
            REQUEST_COUNT.labels(status="inactive_or_not_found", corrected=str(corrected).lower()).inc()
            return JSONResponse({
                "error": status_message, 
                "inn_used": used_inn, 
                "corrected": corrected
            }, status_code=400)

        brand_data = get_brand_data(used_inn)
        brand_name = brand_data.get('value', '') if brand_data else ''
        company_name = company_data.get('value') or company_data.get('name', {}).get('full_with_opf', used_inn)
        
        search_results = search_google(company_name, used_inn, brand_name)
        gen_result = generate_profile_and_recommendations(company_data, brand_data, search_results, used_inn)
        
        # Проверка на ошибку генерации
        if "error" in gen_result:
            REQUEST_COUNT.labels(status="error", corrected=str(corrected).lower()).inc()
            return JSONResponse({"error": f"Ошибка LLM: {gen_result['error']}"}, status_code=500)
        
        final_result = {
            "inn_used": used_inn,
            "corrected": corrected,
            "company_name": company_name,
            "profile": gen_result.get("profile", "Не удалось сформировать профиль"),
            "recommendations": gen_result.get("recommendations", []),
            "is_improved": False
        }
        
        REQUEST_COUNT.labels(status="success", corrected=str(corrected).lower()).inc()
        REQUEST_LATENCY.observe(time.time() - start_time)
        
        return JSONResponse(final_result)

    except Exception as e:
        REQUEST_COUNT.labels(status="error", corrected="false").inc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/evaluate_inn")
async def evaluate_inn(request: Request):
    body = await request.json()
    inn = body.get("inn")
    initial_response = body.get("initial_response")
    
    if not inn or not initial_response:
        return JSONResponse({"error": "Недостаточно данных для оценки"}, status_code=400)

    try:
        company_data, _, _, _ = get_company_data(inn)
        if not company_data:
            return JSONResponse({"error": "Данные компании не найдены"}, status_code=400)
            
        judge_result = evaluate_and_improve_response(company_data, initial_response)
        
        if "error" in judge_result:
            return JSONResponse({"error": judge_result["error"]}, status_code=500)
            
        return JSONResponse({
            "is_improved": True,
            "judge_reliability": judge_result.get("judge_reliability"),
            "judge_completeness": judge_result.get("judge_completeness"),
            "judge_relevance": judge_result.get("judge_relevance"),
            "judge_comment": judge_result.get("judge_comment"),
            "improved_profile": judge_result.get("improved_profile"),
            "improved_recommendations": judge_result.get("improved_recommendations")
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
