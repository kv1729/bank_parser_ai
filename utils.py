import time
import json
import os

def estimate_tokens(text):
    """
    Rough estimation of tokens. Assuming ~4 characters per token.
    """
    return len(text) // 4

def log_metrics(latency, input_tokens, output_tokens, output_file='outputs/metrics.json'):
    """
    Log performance metrics to a JSON file.
    """
    metrics = {
        'latency_seconds': latency,
        'estimated_input_tokens': input_tokens,
        'estimated_output_tokens': output_tokens,
        'timestamp': time.time()
    }
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w') as f:
        json.dump(metrics, f, indent=4)
    print(f"Metrics logged to {output_file}")

def save_json(data, file_path):
    """
    Save data to JSON file.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)

def load_json(file_path):
    """
    Load data from JSON file.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return None