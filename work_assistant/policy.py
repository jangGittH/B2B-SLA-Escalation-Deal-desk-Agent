"""Server-owned tool risk policy. Tier 2 execution is restricted to the durable approval service, never model dispatch."""
TOOL_RISK = {
    'count_workspace_cases':0,
    'inspect_artifact':0, 'propose_memory':1,
    'list_deals': 0, 'get_deal': 0, 'calculate_deal_terms': 0,
    'start_deal_workflow': 1, 'get_deal_workflow': 0, 'resume_deal_workflow': 1,
    'search_knowledge': 0, 'read_document': 0, 'list_sla_cases': 0,
    'get_sla_case': 0, 'calculate_sla_impact': 0,
    'start_sla_workflow': 1, 'get_sla_workflow': 0, 'resume_sla_workflow': 1,
    'write_file': 1, 'draft_incident_memo': 1, 'export_incident_memo': 1,
    'issue_credit': 2, 'send_customer_response': 2, 'update_contract': 2,
}


def allowed_tool(name):
    return name in TOOL_RISK and TOOL_RISK[name] < 2
