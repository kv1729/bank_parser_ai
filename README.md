# Bank Statement Parser

A production-oriented prototype for parsing bank statements using local LLMs via Ollama.

## Features

- PDF text extraction with caching
- Region segmentation
- Hybrid heuristic + LLM extraction
- JSON validation and retry
- Performance tracking

## Architecture

Pipeline:
1. PDF text extraction (pdfminer.six + pdfplumber)
2. Cache extracted text
3. Region segmentation (header, account_details, transaction)
4. Heuristic extraction (bank_name, account_number, IFSC)
5. LLM extraction (account_holder_name)
6. JSON validation
7. Metrics logging

## Setup

1. Install Ollama and pull the model:
   ```
   ollama pull qwen:4b
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Place a sample PDF in `sample_data/sample_statement.pdf`

## Running

```
python app.py
```

## Output

- Extracted data: `outputs/output.json`
- Metrics: `outputs/metrics.json`
- Cached text: `cache/extracted.txt`
- Segmented regions: `cache/segmented.json`

## Example Output

```json
{
    "bank_name": "HDFC BANK",
    "account_holder_name": "John Doe",
    "account_number": "1234567890",
    "ifsc": "HDFC0001234"
}
```

## Future Improvements

- Add transaction extraction
- Improve region segmentation with ML
- Add more validation rules
- Support multiple PDF formats
- Add GUI interface
