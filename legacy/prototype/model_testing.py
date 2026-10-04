import time
import requests

MODELS = ["phi3:mini", "qwen:4b", "gemma:2b", "mistral"]

def test_model(model):
    prompt = """
    Return ONLY this JSON and nothing else:
    {"name": "test", "amount": 100}
    """

    start = time.time()

    try:
        res = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "num_predict": 50,   # VERY IMPORTANT
                "temperature": 0     # reduce randomness
            },
            timeout=60
        )

        end = time.time()

        output = res.json()["response"]

        print(f"\n===== {model} =====")
        print("Latency:", round(end - start, 2), "sec")
        print("Output:", output.strip()[:300])

    except Exception as e:
        print(f"\n===== {model} ERROR =====")
        print(e)


for m in MODELS:
    test_model(m)