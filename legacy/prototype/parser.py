import pdfminer.high_level
import pdfplumber
import os
from utils import save_json

def extract_text_pdfminer(pdf_path):
    """
    Extract text using pdfminer.six
    """
    return pdfminer.high_level.extract_text(pdf_path)

def extract_text_pdfplumber(pdf_path):
    """
    Extract text using pdfplumber
    """
    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        for page in pdf.pages:
            text += page.extract_text() + "\n"
    return text

def extract_and_cache_text(pdf_path, cache_file='cache/extracted.txt'):
    """
    Extract text from PDF, compare methods, cache the best one.
    """
    if os.path.exists(cache_file):
        print("Using cached text.")
        with open(cache_file, 'r') as f:
            return f.read()

    text_pdfminer = extract_text_pdfminer(pdf_path)
    text_pdfplumber = extract_text_pdfplumber(pdf_path)

    # Compare: prefer the one with more characters or lines
    if len(text_pdfminer) >= len(text_pdfplumber):
        text = text_pdfminer
        method = "pdfminer"
    else:
        text = text_pdfplumber
        method = "pdfplumber"

    # Save to cache
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, 'w') as f:
        f.write(text)

    # Print stats
    char_count = len(text)
    line_count = len(text.split('\n'))
    print(f"Extracted text using {method}: {char_count} characters, {line_count} lines.")

    return text

def segment_regions(text):
    """
    Segment text into regions: header, account_details, transaction.
    """
    lines = text.split('\n')
    header = '\n'.join(lines[:20])  # First 20 lines approx header
    account_details = '\n'.join(lines[20:40])  # Next 20 lines
    transaction = '\n'.join(lines[40:])  # Rest

    regions = {
        'header_section': header,
        'account_details_section': account_details,
        'transaction_section': transaction
    }

    # Save segmented outputs
    save_json(regions, 'cache/segmented.json')

    return regions