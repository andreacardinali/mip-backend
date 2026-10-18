import math

def _mean(xs): return sum(xs)/len(xs) if xs else None
def rsi(closes, period=14):
    if len(closes)<=period:return None
    changes=[closes[i]-closes[i-1] for i in range(1,len(closes))][-period:]
    gains=_mean([max(v,0) for v in changes]); losses=_mean([max(-v,0) for v in changes])
    if not losses:return 100.0
    return 100-(100/(1+gains/losses))
def sma(values,period): return _mean(values[-period:]) if len(values)>=period else None
def analyze(rows):
    closes=[r["close"] for r in rows]
    if len(closes)<2:return None
    current,previous=closes[-1],closes[-2]
    change=(current/previous-1)*100 if previous else 0
    last20=closes[-20:]
    avg=_mean(last20); std=math.sqrt(_mean([(x-avg)**2 for x in last20])) if len(last20)>=2 else 0
    bb_width=((4*std)/avg*100) if avg else None
    return {"marketPrice":current,"previousClose":previous,"dailyChangePct":change,
            "rsi":rsi(closes),"sma50":sma(closes,50),"sma200":sma(closes,200),"bbWidth":bb_width,
            "observedDate":rows[-1]["timestamp"]}
def severity(change_pct, allocation_pct):
    score=abs(change_pct)+(allocation_pct/10)
    if score>=12:return "CRITICAL"
    if score>=8:return "HIGH"
    if score>=5:return "MEDIUM"
    return "LOW"
