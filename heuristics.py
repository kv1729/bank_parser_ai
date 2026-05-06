import re

def extract_bank_name(text):
    """
    Heuristic extraction for bank name using regex.
    """
    patterns = [
        r'(HDFC\s*BANK|ICICI\s*BANK|SBI|AXIS\s*BANK|PUNJAB\s*NATIONAL\s*BANK)',
        r'Bank\s*Name\s*:\s*([A-Za-z\s]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None

def extract_account_number(text):
    """
    Heuristic extraction for account number.
    """
    patterns = [
        r'Account\s*No\s*:\s*(\d+)',
        r'Account\s*Number\s*:\s*(\d+)',
        r'A/c\s*No\s*:\s*(\d+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None

def extract_ifsc(text):
    """
    Heuristic extraction for IFSC code.
    """
    pattern = r'IFSC\s*:\s*([A-Z]{4}\d{7})'
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None