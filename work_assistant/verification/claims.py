"""A critic cannot override provenance or deterministic execution boundaries."""
import json
import re
from decimal import Decimal
from ..telemetry import source_reference

class EvidenceChanged(Exception): pass
class Denied(Exception): pass


def current_sources(knowledge, actor_id, evidence):
    fresh={}
    for ident,previous in evidence.items():
        try: doc=knowledge.read(actor_id,ident,previous.language)
        except LookupError: raise Denied('Required evidence is no longer available') from None
        if source_reference(doc)!=source_reference(previous): raise EvidenceChanged('Source version changed')
        fresh[ident]=doc
    return fresh


def validate_claims(claims,evidence):
    errors=[]
    for index,claim in enumerate(claims):
        if claim.kind=='fact' and not claim.evidence:
            errors.append({'index':index,'code':'missing_support'});continue
        for support in claim.evidence:
            doc=evidence.get(support.source_id)
            if doc is None or support.version!=doc.version or support.quote not in doc.content:
                errors.append({'index':index,'code':'invalid_source_version_or_quote'})
        if claim.kind!='fact':continue
        # Numbers cannot appear from nowhere even if a semantic critic agrees.
        # Source identifiers and equivalent decimal display formats are not new quantities.
        claim_text=re.sub(r'\b(?:(?:INC|DEAL)-[\w-]+|P[1-3])\b','',claim.text)
        claim_text=re.sub(r'(?<=\d),(?=\d{3}\b)','',claim_text)
        numbers=[(Decimal(n),bool(percent)) for n,percent in re.findall(r'(\d+(?:\.\d+)?)(\s*[%％])?',claim_text)]
        quoted=' '.join(s.quote for s in claim.evidence)
        quoted=re.sub(r'(?<=\d),(?=\d{3}\b)','',quoted)
        quoted_numbers={Decimal(n) for n in re.findall(r'\d+(?:\.\d+)?',quoted)}
        # Exact unit conversion only: a quoted fraction 0.10 supports 10%.
        # Semantic verification still checks which field the number describes.
        if any(n not in quoted_numbers and not (percent and n/100 in quoted_numbers) for n,percent in numbers):
            errors.append({'index':index,'code':'unsupported_number'})
        # Positive completion claims require typed receipt state, never policy prose.
        completed=re.search(r'\b(?:is|was|has been|have been)\s+(?:approved|paid|issued|sent|resolved)\b|\b(?:issued|paid|sent)\s+(?:the|a|customer)\b|已(?:批准|支付|发送|解决|发放)',claim.text,re.I)
        root=re.search(r'\b(?:confirmed|definitive)\s+root cause\b|\broot cause (?:is|was)\s+(?!(?:not|unknown|unconfirmed|undetermined)\b)|根因已确认|根因是',claim.text,re.I)
        if completed and re.search(r'\b(?:not|neither|no)\b|未|没有',claim.text[max(0,completed.start()-35):completed.start()],re.I):
            completed=None
        if root:errors.append({'index':index,'code':'root_cause_not_independently_verified'})
        if completed:
            verified=False
            for support in claim.evidence:
                doc=evidence.get(support.source_id)
                if doc and doc.id.startswith(('workflow:','deal-workflow:')):
                    row=json.loads(doc.content)
                    verified=verified or bool(row.get('status')=='completed' and row.get('receipt') and (row.get('outcome') or {}).get('verified'))
            if not verified:errors.append({'index':index,'code':'execution_receipt_required'})
            # No connector or technical-resolution evidence exists in this sandbox.
            if re.search(r'\b(?:sent|resolved|paid)\b|已(?:支付|发送|解决)',claim.text,re.I):
                errors.append({'index':index,'code':'external_completion_unavailable'})
    return errors


def review_errors(claims,review):
    indices=[r.index for r in review.verdicts]
    if sorted(indices)!=list(range(len(claims))):return [{'index':-1,'code':'critic_coverage_incomplete'}]
    return [{'index':r.index,'code':r.verdict,'reason':r.reason} for r in review.verdicts if r.verdict!='supported']


def render(claims,language):
    labels={'hypothesis':('Hypothesis (unverified)','假设（未经验证）'),
            'suggestion':('Suggestion','建议'),'unknown':('Unknown','未知')}
    return '\n\n'.join(c.text if c.kind=='fact' else labels[c.kind][language=='zh']+': '+c.text for c in claims)
