import json
from datetime import date, timedelta
from pathlib import Path
from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.llm.base import LLMRequest
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.entities import load_candidates
from jobs.portfolio_intelligence.repositories.catalysts import recent_catalysts

def _batches(items,size):
    for i in range(0,len(items),size): yield items[i:i+size]
def _sev(days,weight):
    score=(3 if days<=1 else 2 if days<=3 else 1)+(3 if weight>=20 else 2 if weight>=5 else 1)
    return ("CRITICAL" if score>=6 else "HIGH" if score>=5 else "MEDIUM" if score>=4 else "LOW",score)

class UpcomingCatalystBranch(IntelligenceBranch):
    name="UPCOMING_CATALYST"
    def execute(self, context):
        result=BranchResult(branch=self.name)
        candidates=load_candidates(context.db); result.candidate_count=len(candidates)
        today=date.today(); end=today+timedelta(days=context.config.upcoming_window_days)
        known=recent_catalysts(context.db,[c.entity_id for c in candidates],30)
        prompt_tpl=(Path(__file__).parent.parent/'prompts/upcoming.txt').read_text()
        accepted=[]
        try:
            for batch in _batches(candidates,context.config.batch_size):
                payload=[{**c.to_dict(),"recentCatalysts":known.get(c.entity_id,[])} for c in batch]
                response=context.gemini.generate_json(LLMRequest(prompt=prompt_tpl.format(execution_date=date.today().isoformat(),window_start=today.isoformat(),window_end=end.isoformat(),candidates=json.dumps(payload,default=str)),grounded_search=True,max_tokens=8192))
                rows=response.data.get("results") or []
                if len(rows)!=len(batch): raise ValueError("Upcoming Gemini result count mismatch")
                for source, row in zip(batch,rows):
                    if row.get("symbol")!=source.symbol: raise ValueError("Upcoming Gemini symbol mismatch")
                    for event in row.get("events") or []:
                        try: event_date=date.fromisoformat(event["eventDate"][:10])
                        except Exception: continue
                        if not today<=event_date<=end: continue
                        if not (0<=int(event.get("eventConfidence",0))<=100): continue
                        days=(event_date-today).days; severity,score=_sev(days,source.allocation_pct)
                        key=f"{source.symbol}|{event.get('eventType')}|{event_date.isoformat()}"
                        item={**event,"user_id":source.user_id,"entity_id":source.entity_id,"symbol":source.symbol,"name":source.name,"eventKey":key,"daysUntilEvent":days,"severity":severity,"severityScore":score,"sources":response.sources}
                        accepted.append(item)
            dedup = {
                        (
                            x["user_id"],
                            x["eventKey"],
                        ): x
                        for x in accepted
                    }

            accepted = list(
                dedup.values()
            )
            
            result.accepted_count = len(
                accepted
            )
            for e in accepted:
                message = (f"📅 UPCOMING CATALYST\n{e['symbol']} | {e.get('eventType')} | {e.get('eventDate')}\n"
                           f"Severity: {e['severity']}\n{e.get('headline','')}\n{e.get('eventSummary','')}")
                if context.notifications.publish(user_id=e['user_id'],notification_id=f"UPCOMING_CATALYST|{e['eventKey']}",branch=self.name,entity_id=e['entity_id'],symbol=e['symbol'],event_key=e['eventKey'],severity=e['severity'],title=e.get('headline') or e['eventKey'],message=message,context=e): result.notification_count+=1
            context.db.commit(); result.diagnostics={"windowStart":today.isoformat(),"windowEnd":end.isoformat()}
        except Exception as exc:
            context.db.rollback(); result.status="FAILED"; result.errors.append(str(exc))
        return result

def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(UpcomingCatalystBranch())
