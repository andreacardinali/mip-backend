from urllib.parse import quote
import httpx

def load_history(symbol: str, history_range: str, interval: str, timeout: float) -> list[dict]:
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol,safe='')}"
    with httpx.Client(timeout=timeout,headers={"User-Agent":"Mozilla/5.0 mip-scheduler"}) as client:
        r=client.get(url,params={"range":history_range,"interval":interval,"events":"div,splits"}); r.raise_for_status(); payload=r.json()
    result=((payload.get("chart") or {}).get("result") or [None])[0]
    if not result: return []
    q=((result.get("indicators") or {}).get("quote") or [{}])[0]
    rows=[]
    for idx,ts in enumerate(result.get("timestamp") or []):
        values={k:(q.get(k) or [None]*len(result.get("timestamp") or []))[idx] for k in ("open","high","low","close","volume")}
        if any(values[k] is None for k in ("open","high","low","close")): continue
        rows.append({"timestamp":ts,**{k:float(v) if v is not None else None for k,v in values.items()}})
    return rows
