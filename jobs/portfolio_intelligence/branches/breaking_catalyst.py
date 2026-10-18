import json
from datetime import datetime, timezone
from pathlib import Path
from jobs.portfolio_intelligence.branches.base import IntelligenceBranch
from jobs.portfolio_intelligence.llm.base import LLMRequest
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.repositories.entities import load_candidates

def _batches(items,size):
    for i in range(0,len(items),size): yield items[i:i+size]
class BreakingCatalystBranch(IntelligenceBranch):
    name="BREAKING_CATALYST"
    def execute(self,context):
        result=BranchResult(branch=self.name); candidates=load_candidates(context.db); result.candidate_count=len(candidates)
        tpl=(Path(__file__).parent.parent/'prompts/breaking.txt').read_text(); accepted=[]
        try:
            for batch in _batches(candidates,context.config.batch_size):
                response=context.research_llm.generate_json(LLMRequest(prompt=tpl.format(lookback_hours=context.config.breaking_lookback_hours,execution_date=datetime.now(timezone.utc).isoformat(),candidates=json.dumps([c.to_dict() for c in batch],default=str)),grounded_search=True,max_tokens=8192))
                rows=response.data.get("results") or []
                if len(rows)!=len(batch): raise ValueError("Breaking LLM result count mismatch")
                for source,row in zip(batch,rows):
                    if row.get("symbol")!=source.symbol: raise ValueError("Breaking LLM symbol mismatch")
                    for event in row.get("events") or []:
                        if int(event.get("eventConfidence",0))<60: continue
                        fingerprint=str(event.get("eventFingerprint") or "").strip()
                        day=str(event.get("eventDate") or "")[:10]
                        if not fingerprint or not day: continue
                        key=f"{source.symbol}|{event.get('eventType')}|{day}|{fingerprint}"
                        confidence=int(event.get("eventConfidence",0)); severity="CRITICAL" if confidence>=90 and source.allocation_pct>=20 else "HIGH" if confidence>=80 else "MEDIUM"
                        accepted.append({**event,"user_id":source.user_id,"entity_id":source.entity_id,"symbol":source.symbol,"name":source.name,"eventKey":key,"severity":severity,"sources":response.sources})
            accepted = list(
                {
                    (
                        e["user_id"],
                        e["eventKey"],
                    ): e
                    for e in accepted
                }.values()
            )
            result.accepted_count = len(
                accepted
            )
            for e in accepted:
                message = (f"⚡ BREAKING CATALYST\n{e['symbol']} | {e.get('eventType')}\n"
                           f"Severity: {e['severity']}\n{e.get('headline','')}\n{e.get('eventSummary','')}\n"
                           f"{e.get('materialityReason','')}")
                if context.notifications.publish(user_id=e['user_id'],notification_id=f"BREAKING_CATALYST|{e['eventKey']}",branch=self.name,entity_id=e['entity_id'],symbol=e['symbol'],event_key=e['eventKey'],severity=e['severity'],title=e.get('headline') or e['eventKey'],message=message,context=e): result.notification_count+=1
            context.db.commit(); result.diagnostics={"lookbackHours":context.config.breaking_lookback_hours}
        except Exception as exc:
            context.db.rollback(); result.status="FAILED"; result.errors.append(str(exc))
        return result

def run():
    from jobs.portfolio_intelligence.runtime import run_branch
    return run_branch(BreakingCatalystBranch())
