#!/usr/bin/env python3
"""Reproduce the assignment cost arithmetic from the supplied immutable rates."""
import json
from pathlib import Path
from collections import Counter
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import server
rates = json.loads((root / 'source/VIVI_MVP_Sample_Dataset/pricing_assumptions.json').read_text())
unit = {x['cost_id']: x['unit_cost_usd'] for x in rates['cost_items']}
requests = rates['monthly_requests_per_customer']
customers = rates['initial_customers']
revenue = rates['customer_monthly_subscription_usd']
weekly_collections = next(x for x in rates['collection']['reference_frequencies'] if x['frequency'] == 'weekly')
collection_cost = weekly_collections['cost_usd']
base = requests * (unit['AI-M'] + unit['API001']) + collection_cost + unit['INF001']

# Executed MVP: local evidence joins + deterministic rules; no model or external
# analysis API is called by the server. Mock pricing is not applied to omitted calls.
optimized = collection_cost + unit['INF001']
margin = (revenue - optimized) / revenue
threshold_paid_misses = int((rates['maximum_cogs_per_customer_usd'] - optimized) // (unit['AI-M'] + unit['API001']))
analyses = [server.analysis(q) for q in server.QUESTIONS]
decisions = Counter(a['decision'] for a in analyses)

print(json.dumps({
    'baseline_per_customer_usd': round(base, 2),
    'baseline_margin_pct': round((revenue-base)/revenue*100, 2),
    'implemented_per_customer_usd': round(optimized, 2),
    'implemented_margin_pct': round(margin*100, 2),
    'implemented_100_customers_usd': round(optimized*customers, 2),
    'maximum_paid_ai_m_plus_api_misses_if_added': threshold_paid_misses,
    'latest_valid_observations': sum(a['validCount'] for a in analyses),
    'latest_unknown_observations': sum(a['unknownCount'] for a in analyses),
    'rule_decisions': dict(decisions),
    'note': 'The implemented rule engine executes zero model/API calls. Collection and infrastructure costs are still charged at supplied rates. This is a fixed-rate design estimate, not a measured production bill; raw-answer parsing, future labeling, CPU growth, and customer load are not priced.'
}, ensure_ascii=False, indent=2))
assert optimized <= rates['maximum_cogs_per_customer_usd']
assert round(base, 2) == 39.87
