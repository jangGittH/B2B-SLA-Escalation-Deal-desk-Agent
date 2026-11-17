"""Deterministic provider double. Consumes only the request and actual tool observations."""
import copy
import json
import re
import anyio
from work_assistant.agents.model import StructuredReply

class CaseModel:
    model='deterministic-case-provider-v1'
    def __init__(self, fault=None):
        self.calls=[];self.fault=fault;self.fired=False;self.closed=False
    async def aclose(self):self.closed=True
    async def structured(self,role,payload,schema,language,output_tokens=2048):
        self.calls.append((role,copy.deepcopy(payload)))
        await anyio.sleep(.002)
        if role=='intake':return StructuredReply({'route':'investigate','reply':''})
        if role=='conversation_guard':return StructuredReply({'safe':True,'reason':'Conversational reply only.'})
        question=payload.get('question','');match=re.search(r'\b(INC-\d+|DEAL-\d+)\b',question)
        subject=match.group() if match else None
        deal=bool(subject and subject.startswith('DEAL'))
        if role=='supervisor':
            if not subject and 'response' not in question.lower() and '响应' not in question:
                return StructuredReply({'decision':'clarify','nodes':[],'terminal':''})
            nodes=[{'id':'knowledge','role':'knowledge','objective':'Read current SLA or commercial evidence',
                'dependencies':[],'tools':['read_document','search_knowledge','inspect_artifact'],
                'required_inputs':[],'output_schema':'handoff_v1','completion':'evidence_read','max_calls':3}]
            dependencies=['knowledge']
            if subject:
                nodes.append({'id':'analyst','role':'analyst','objective':'Calculate current stored terms',
                    'dependencies':[],'tools':['calculate_deal_terms' if deal else 'calculate_sla_impact'],
                    'required_inputs':[],'output_schema':'handoff_v1','completion':'calculation_checked','max_calls':3})
                dependencies.append('analyst')
            if subject and ('review' in question.lower() or 'draft' in question.lower()):
                nodes.append({'id':'action','role':'action','objective':'Prepare the requested private draft or human review',
                    'dependencies':dependencies[:],'tools':['start_deal_workflow' if deal else 'start_sla_workflow','draft_incident_memo','export_incident_memo'],
                    'required_inputs':dependencies[:],'output_schema':'handoff_v1','completion':'draft_ready','max_calls':4})
                dependencies=['action']
            nodes.append({'id':'critic','role':'critic','objective':'Verify every final claim','dependencies':dependencies,
                'tools':[],'required_inputs':dependencies,'output_schema':'claim_verdicts_v1','completion':'claims_verified','max_calls':1})
            return StructuredReply({'decision':'execute','nodes':nodes,'terminal':'critic'})
        if role=='critic':
            return StructuredReply({'verdicts':[{'index':c['index'],'verdict':'unsupported' if 'guaranteed resolution' in c['claim']['text'] else 'supported',
                'reason':'The excerpt states an initial response target, not guaranteed resolution.' if 'guaranteed resolution' in c['claim']['text'] else 'Supported by the supplied scoped evidence.'} for c in payload['claims']]})
        if role=='repair':
            draft=copy.deepcopy(payload['draft'])
            for claim in draft['claims']:
                if 'guaranteed resolution' in claim['text']:claim['text']=claim['evidence'][0]['quote']
            return StructuredReply(draft)
        observations=payload.get('observations',[])
        if role=='single':
            if not observations:
                if not subject:return self.tool('read_document',{'document_id':'acme-sla'})
                return self.tool('calculate_deal_terms' if deal else 'calculate_sla_impact',{'deal_id' if deal else 'ticket_id':subject})
            if len(observations)==1 and 'review' in question.lower():
                doc=observations[-1]['result'].get('document')
                if doc:
                    record=json.loads(doc['content']);row=record['deal' if deal else 'ticket']
                    return self.tool('start_deal_workflow' if deal else 'start_sla_workflow',
                        {'deal_id' if deal else 'ticket_id':subject,'expected_deal_version' if deal else 'expected_ticket_version':row['version']})
        if role=='knowledge' and not observations:
            return self.tool('read_document',{'document_id':'acme-deal' if deal else 'acme-sla'})
        if role=='analyst' and not observations:
            return self.tool('calculate_deal_terms' if deal else 'calculate_sla_impact',{'deal_id' if deal else 'ticket_id':subject})
        if role=='action' and not observations:
            doc=next((d for d in payload['sources'] if d['id']==('deal:' if deal else 'case:')+subject),None)
            if not doc:return StructuredReply({'result':{'status':'needs_input','claims':[],'source_ids':[],'unresolved':['Authorized case evidence required']}})
            record=json.loads(doc['content']);row=record['deal' if deal else 'ticket']
            if 'draft' in question.lower() and not deal:
                return self.tool('draft_incident_memo',{'ticket_id':subject,'expected_ticket_version':row['version']})
            return self.tool('start_deal_workflow' if deal else 'start_sla_workflow',
                {'deal_id' if deal else 'ticket_id':subject,'expected_deal_version' if deal else 'expected_ticket_version':row['version']})
        if observations and not observations[-1]['result'].get('ok'):
            return StructuredReply({'result':{'status':'not_found','claims':[],'source_ids':[],'unresolved':['Source unavailable']}})
        documents=[o['result']['document'] for o in observations if o['result'].get('document')]
        if not documents:return StructuredReply({'result':{'status':'needs_input','claims':[],'source_ids':[],'unresolved':['Provide current evidence']}})
        claims=[]
        for doc in documents[-2:]:
            text=doc['content']
            if doc['id'].startswith(('case:','deal:')):
                record=json.loads(text);calc=record['calculation']
                quote='"status": '+json.dumps(calc['status'])
            elif doc['id'].startswith('workflow-request:'):
                quote=json.loads(text)['note']
            else:
                quote=next((sentence+'.' for sentence in text.split('. ') if '60 minutes' in sentence),text[:900])
                if quote not in text:quote=text[:900]
            quote=quote[:1500]
            claims.append({'kind':'fact','text':quote[:1000],'evidence':[{'source_id':doc['id'],'version':doc['version'],'quote':quote}]})
        if self.fault=='false_claim' and not self.fired:
            self.fired=True;claims[0]['text']='The contract promises guaranteed resolution within 60 minutes.'
        return StructuredReply({'result':{'status':'complete','claims':claims,'source_ids':[d['id'] for d in documents],'unresolved':[]}})
    @staticmethod
    def tool(name,arguments):return StructuredReply({'calls':[{'name':name,'arguments':arguments}]})
