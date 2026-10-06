"""End-to-end HTTP check against an already-running deployment."""
import argparse
import httpx

parser = argparse.ArgumentParser()
parser.add_argument('base_url')
parser.add_argument('--case-id', default='SIM-001')
parser.add_argument('--expect-scored', action='store_true')
args = parser.parse_args()
with httpx.Client(base_url=args.base_url, timeout=120) as client:
    health = client.get('/api/health'); health.raise_for_status()
    assert health.json()['status'] == 'ok'
    cases = client.get('/api/cases/recommended'); cases.raise_for_status()
    assert cases.json()
    if args.expect_scored and not any(case['case_id'] == args.case_id and case['status'] == 'approved' for case in cases.json()):
        raise RuntimeError('Requested case needs clinical approval before scored smoke test')
    started = client.post('/api/simulation/start', json={'case_id': args.case_id, 'resident_name': 'Smoke test'}); started.raise_for_status()
    sid = started.json()['session_id']
    try:
        for question in ['Tell me about the pain', 'When did it start?', 'Any medication allergies?']:
            client.post('/api/simulation/history', json={'session_id': sid, 'question': question}).raise_for_status()
        client.post('/api/simulation/exam', json={'session_id': sid, 'system': 'General'}).raise_for_status()
        client.post('/api/simulation/investigate', json={'session_id': sid, 'investigation': 'ECG'}).raise_for_status()
        result = client.post('/api/simulation/submit', json={'session_id': sid, 'diagnosis': 'Smoke test diagnosis', 'management': ['Escalate to supervisor']}); result.raise_for_status()
        assert bool(result.json()['scores']) == args.expect_scored
        for format in ['json', 'pdf']:
            export = client.get(f'/api/simulation/session/{sid}/export/{format}')
            export.raise_for_status()
            assert export.content
    finally:
        client.delete(f'/api/simulation/session/{sid}').raise_for_status()
print('Health, cases, session, history, exam, investigation, submission, export and deletion: OK')
