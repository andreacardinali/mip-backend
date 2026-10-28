from html.parser import HTMLParser


class HTMLTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append({"table": [], "row": None, "cell": None})
        elif self._stack:
            state = self._stack[-1]
            if tag == "tr":
                state["row"] = []
            elif tag in {"th", "td"} and state["row"] is not None:
                state["cell"] = []

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None:
            self._stack[-1]["cell"].append(data)

    def handle_endtag(self, tag):
        if not self._stack:
            return
        state = self._stack[-1]
        if tag in {"th", "td"} and state["cell"] is not None:
            if state["row"] is not None:
                state["row"].append(" ".join("".join(state["cell"]).split()))
            state["cell"] = None
        elif tag == "tr":
            if state["row"]:
                state["table"].append(state["row"])
            state["row"] = None
        elif tag == "table":
            if state["table"]:
                self.tables.append(state["table"])
            self._stack.pop()