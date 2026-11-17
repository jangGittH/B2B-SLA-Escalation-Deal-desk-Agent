"""Versioned, expiring text-only list-price estimates. Unknown is never zero."""
from datetime import datetime,timezone
from decimal import Decimal
import json
from pathlib import Path

SCHEDULE=json.loads(Path(__file__).with_name('prices.json').read_text())

def estimate(model,usage,at=None):
    at=at or datetime.now(timezone.utc)
    unknown={'cost_usd':None,'pricing_version':None,'cost_basis':SCHEDULE['basis']}
    rate=SCHEDULE['models'].get(model)
    if not rate or not usage or not datetime.fromisoformat(SCHEDULE['valid_from'])<=at<datetime.fromisoformat(SCHEDULE['valid_until']):return unknown
    prompt=usage.get('prompt_token_count');output=usage.get('candidates_token_count')
    cached=usage.get('cached_content_token_count') or 0;thinking=usage.get('thoughts_token_count') or 0
    total=usage.get('total_token_count')
    if any(type(n) is not int or n<0 for n in (prompt,output,cached,thinking,total)):return unknown
    if cached>prompt or prompt+output+thinking!=total:return unknown
    for detail in usage.get('prompt_tokens_details') or []:
        if str(detail.get('modality','')).upper() not in ('TEXT','MEDIAMODALITY.TEXT'):return unknown
    cost=(Decimal(prompt-cached)*Decimal(rate['input_per_million'])+Decimal(cached)*Decimal(rate['cached_input_per_million'])+
          Decimal(output+thinking)*Decimal(rate['output_per_million']))/Decimal(1000000)
    return {'cost_usd':str(cost),'pricing_version':SCHEDULE['version'],'cost_basis':SCHEDULE['basis']}
