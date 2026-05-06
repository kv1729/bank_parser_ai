import requests
import json
import time

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen:4b"

def call_llm(prompt, temperature=0, num_predict=100, timeout=30):
    """
    Call Ollama API for LLM extraction.
    """
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict
        }
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        return result.get('response', '').strip()
    except requests.RequestException as e:
        print(f"LLM call failed: {e}")
        return None

def extract_with_llm(text, field):
    """
    Extract a specific field using LLM.
    """
    prompt = f"""
Extract the {field} from the following bank statement text. Return only a JSON object with the key "{field}" and its value. No explanations, no markdown.

Text:
{text}

Output:
"""
    response = call_llm(prompt)
    if response:
        try:
            data = json.loads(response)
            return data.get(field)
        except json.JSONDecodeError:
            print(f"Invalid JSON from LLM for {field}")
            return None
    return None