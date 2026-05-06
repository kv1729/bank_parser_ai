from heuristics import extract_bank_name, extract_account_number, extract_ifsc
from llm import extract_with_llm

def extract_fields(text):
    """
    Extract fields using hybrid approach: heuristics first, LLM for uncertain fields.
    """
    extracted = {}

    # Heuristic extraction
    extracted['bank_name'] = extract_bank_name(text)
    extracted['account_number'] = extract_account_number(text)
    extracted['ifsc'] = extract_ifsc(text)

    # LLM for account_holder_name
    if not extracted.get('account_holder_name'):
        extracted['account_holder_name'] = extract_with_llm(text, 'account_holder_name')

    return extracted