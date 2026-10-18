def validated_sources(indices: list, sources: list[dict]) -> list[dict]:
    by_index={int(s["sourceIndex"]):s for s in sources if isinstance(s.get("sourceIndex"),int)}
    clean=[]
    for value in indices or []:
        if isinstance(value,int) and value in by_index and value not in [s["sourceIndex"] for s in clean]: clean.append(by_index[value])
    return clean
