import httpx
class TelegramNotifier:
    def __init__(self, token: str|None, chat_id: str|None, timeout: float=30): self.token=token; self.chat_id=chat_id; self.timeout=timeout
    def send(self, message: str) -> list[dict]:
        if not self.token or not self.chat_id: raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        chunks=[message[i:i+3900] for i in range(0,len(message),3900)] or [message]
        out=[]
        with httpx.Client(timeout=self.timeout) as client:
            for chunk in chunks:
                r=client.post(f"https://api.telegram.org/bot{self.token}/sendMessage",json={"chat_id":self.chat_id,"text":chunk,"disable_web_page_preview":True}); r.raise_for_status(); out.append(r.json())
        return out
