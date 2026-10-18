import json, re

def parse_json_object(text: str) -> dict:
    value=text.strip()
    if value.startswith("```"):
        value=re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value=re.sub(r"\s*```$", "", value)
    try: data=json.loads(value)
    except json.JSONDecodeError:
        start=value.find("{"); end=value.rfind("}")
        if start<0 or end<=start: raise
        data=json.loads(value[start:end+1])
    if not isinstance(data, dict): raise ValueError("LLM response must be a JSON object")
    return data
