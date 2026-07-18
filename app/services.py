import os
import json
import re
import requests
from dotenv import load_dotenv
from dadata import Dadata
from openai import OpenAI
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

load_dotenv()

DADATA_TOKEN = os.getenv("DADATA_TOKEN", "MY_DADATA")
DADATA_SECRET = os.getenv("DADATA_SECRET", "MY_DADATA_SECRET")
RUSGPT_API_KEY = os.getenv("RUSGPT_API_KEY")

if not RUSGPT_API_KEY or RUSGPT_API_KEY == "rusgpt-APIKEY":
    print("⚠️ ВНИМАНИЕ: API-ключ rus-gpt не найден или не изменен в файле .env!")

dadata = Dadata(DADATA_TOKEN)
client = OpenAI(base_url="https://rus-gpt.com/api/v1", api_key=RUSGPT_API_KEY)


def safe_json_parse(content: str) -> dict:
    """Безопасно извлекает JSON из ответа LLM, удаляя markdown-обертки и мыслительные блоки."""
    try:
        content = re.sub(r'^```(?:json)?\s*', '', content, flags=re.IGNORECASE)
        content = re.sub(r'\s*```$', '', content, flags=re.IGNORECASE)
        
        start_idx = content.find('{')
        end_idx = content.rfind('}')
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            content = content[start_idx:end_idx+1]
            
        return json.loads(content)
    except json.JSONDecodeError as e:
        return {"error": f"Ошибка парсинга JSON: {e}. Начало сырого ответа: {content[:200]}"}
    except Exception as e:
        return {"error": f"Неизвестная ошибка при парсинге: {e}"}


# Агент 1: Поиск и исправление ИНН + Проверка статуса
def get_company_data(inn: str):
    res = dadata.find_by_id("party", inn)
    if res and len(res) > 0:
        item = res[0]
        item_data = item.get('data', {}) or {}
        state = item_data.get('state', {}) or {}
        status = state.get('status')
        liquidation_date = state.get('liquidation_date')
        
        if status != 'ACTIVE' or liquidation_date:
            status_map = {
                'LIQUIDATING': 'ликвидируется',
                'LIQUIDATED': 'ликвидирована',
                'BANKRUPT': 'находится в процессе банкротства',
                'REORGANIZING': 'в процессе присоединения к другому юрлицу с последующей ликвидацией'
            }
            msg = status_map.get(status, 'находится в процессе ликвидации или реорганизации')
            return None, inn, False, f"Компания {msg}."
        return item, inn, False, None
    
    suggest_res = dadata.suggest("party", inn)
    if suggest_res and len(suggest_res) > 0:
        suggested_inn = suggest_res[0]['data']['inn']
        full_res = dadata.find_by_id("party", suggested_inn)
        if full_res and len(full_res) > 0:
            item = full_res[0]
            item_data = item.get('data', {}) or {}
            state = item_data.get('state', {}) or {}
            status = state.get('status')
            liquidation_date = state.get('liquidation_date')
            
            if status != 'ACTIVE' or liquidation_date:
                status_map = {
                    'LIQUIDATING': 'ликвидируется',
                    'LIQUIDATED': 'ликвидирована',
                    'BANKRUPT': 'находится в процессе банкротства',
                    'REORGANIZING': 'в процессе присоединения к другому юрлицу с последующей ликвидацией'
                }
                msg = status_map.get(status, 'находится в процессе ликвидации или реорганизации')
                return None, suggested_inn, True, f"Компания {msg}."
            return item, suggested_inn, True, None
            
    return None, inn, False, "Компания не найдена и не удалось подобрать аналоги."


# Агент 2: Поиск бренда
def get_brand_data(inn: str):
    url = "https://api.dadata.ru/findById/brand"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {DADATA_TOKEN}",
        "X-Secret": DADATA_SECRET
    }
    try:
        response = requests.post(url, headers=headers, json={"query": inn}, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("suggestions"):
                return data["suggestions"][0]
    except Exception:
        pass
    return None


# Агент 3: Поиск в интернете (DDGS)
def search_google(company_name: str, inn: str, brand_name: str = ""):
    query = f"{company_name} ИНН {inn}"
    if brand_name:
        query += f" бренд {brand_name}"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            return "\n".join([f"- {r.get('title', '')}: {r.get('body', '')}" for r in results if r])
    except Exception as e:
        return f"Ошибка поиска: {e}"


# Агент 4: Генерация профиля и рекомендаций
def generate_profile_and_recommendations(company_data: dict, brand_data: dict, search_results: str, inn: str):
    # БЕЗОПАСНОЕ извлечение данных с проверкой на None
    data = company_data.get('data') or {}
    name = company_data.get('value') or 'Неизвестно'
    
    address_data = company_data.get('address')
    address = address_data.get('value', 'Неизвестно') if isinstance(address_data, dict) else 'Неизвестно'
    
    okved = data.get('okved') or 'Неизвестно'
    
    finance_history = data.get('finance_history')
    finance_str = "Нет данных"
    if finance_history and len(finance_history) > 0:
        latest = finance_history[0]
        metrics = latest.get('metrics', []) or []
        f_vals = {m.get('code'): m.get('value') for m in metrics if m and m.get('code')}
        finance_str = f"Выручка (2110): {f_vals.get('2110', 'н/д')}, Чистая прибыль (2400): {f_vals.get('2400', 'н/д')}, Активы (1600): {f_vals.get('1600', 'н/д')}"
    
    employee_count = data.get('employee_count') or 'н/д'
    branch_count = data.get('branch_count') or 'н/д'
    
    finance_data = data.get('finance')
    tax_system = finance_data.get('tax_system') if isinstance(finance_data, dict) else None
    tax_system = tax_system or 'н/д'
    
    citizenship_data = data.get('citizenship')
    citizenship = citizenship_data.get('name', {}).get('full', 'н/д') if isinstance(citizenship_data, dict) else (str(citizenship_data) if citizenship_data else 'н/д')
    
    capital = data.get('capital')
    capital_str = f"{capital.get('value', 'н/д')} {capital.get('type', '')}".strip() if isinstance(capital, dict) else (str(capital) if capital else 'н/д')
    
    brand_val = brand_data.get('value', 'Не найден') if isinstance(brand_data, dict) else 'Не найден'
    
    prompt = f"""
Ты - ассистент клиентского менеджера в коммерческом банке. Твои клиенты - малый и средний бизнес.
Твоя задача:
1) сформировать профиль внешнего клиента по его ИНН {inn};
2) предложить клиенту наиболее релевантные банковские продукты.

Данные о компании:
- Наименование: {name}
- Адрес: {address}
- ОКВЭД: {okved}
- Бренд: {brand_val}
- Количество сотрудников: {employee_count}
- Количество филиалов: {branch_count}
- Система налогообложения: {tax_system}
- Гражданство ИП: {citizenship}
- Уставной капитал: {capital_str}
- Финансовая отчетность: {finance_str}
- Доп. информация из поиска: {search_results}

Требования к ответу (строго в формате JSON):
{{
  "profile": "Профиль клиента (5-10 предложений).",
  "recommendations": [
    {{ "product": "Название продукта", "justification": "Обоснование (2-5 предложений)." }}
  ]
}}
Рекомендованные продукты должны быть расположены по убыванию релевантности. Количество продуктов > 3.
"""
    try:
        response = client.chat.completions.create(
            model="qwen/qwen3.7-plus", 
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": True}
        )
        return safe_json_parse(response.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}


# Агент 5: LLM-as-a-Judge (Оценка и УЛУЧШЕНИЕ)
def evaluate_and_improve_response(company_data: dict, initial_response: dict):
    prompt = f"""
Ты - строгий и опытный оценщик (LLM-as-a-judge) и эксперт по банковским продуктам. 
Твоя задача:
1. Оценить предоставленный профиль и рекомендации по трем критериям (от 1 до 5):
   - Достоверность (сверка с данными DaData)
   - Полнота профиля клиента
   - Релевантность рекомендаций
2. На основе этой оценки, ВЫДАТЬ УЛУЧШЕННУЮ ВЕРСИЮ ответа. Исправь неточности, дополни профиль, сделай обоснования продуктов более убедительными и персонализированными.

Данные компании: {json.dumps(company_data.get('data', {}), ensure_ascii=False)}
Исходный ответ: {json.dumps(initial_response, ensure_ascii=False)}

Ответь строго в формате JSON:
{{
  "judge_reliability": 5,
  "judge_completeness": 5,
  "judge_relevance": 5,
  "judge_comment": "Краткий комментарий оценщика о том, что именно было улучшено.",
  "improved_profile": "Улучшенный профиль клиента (5-10 предложений).",
  "improved_recommendations": [
    {{ "product": "Название продукта", "justification": "Улучшенное обоснование (2-5 предложений)." }}
  ]
}}
"""
    try:
        response = client.chat.completions.create(
            model="anthropic/claude-sonnet-5", 
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": True}
        )
        return safe_json_parse(response.choices[0].message.content)
    except Exception as e:
        return {"error": str(e)}