from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / 'www' / 'app.js').read_text()
APP_PY = (ROOT / 'app.py').read_text()
PROMPT_MD = (ROOT / 'AI_IMPORT_PROMPT.md').read_text()
README = (ROOT / 'README.md').read_text()


class SipTotalAndAiOutputTests(unittest.TestCase):
    def test_table_totals_include_active_sip_amount_total(self):
        self.assertIn("const sipTotal=visible.reduce((sum,f)=>sum+(f.sip_enabled&&Number(f.sip_amount)>0?Number(f.sip_amount):0),0);", APP_JS)
        self.assertIn("<th>${money(sipTotal)}</th>", APP_JS)
        self.assertIn("f.sip_enabled&&Number(f.sip_amount)>0", APP_JS)

    def test_ai_prompt_requests_downloadable_investor_named_json(self):
        self.assertIn('Convert the attached CAMS/KFintech Consolidated Account Statement into the COMPACT Mutual Fund Tracker JSON format below.', APP_PY)
        self.assertIn('ONE fenced Markdown code block labeled `json`', APP_PY)
        self.assertIn('The code block is the PRIMARY and REQUIRED deliverable', APP_PY)
        self.assertIn("Use the code block's download/save control", APP_PY)
        self.assertIn('Convert the attached CAMS/KFintech Consolidated Account Statement into the COMPACT Mutual Fund Tracker JSON format below.', PROMPT_MD)
        self.assertIn('ONE fenced Markdown code block labeled `json`', PROMPT_MD)

    def test_readme_prefers_downloadable_json_file(self):
        self.assertIn('Prefer the downloadable JSON file attached by the AI', README)
        self.assertIn('Noohu_Konnu_Abdul_Salam.json', README)
