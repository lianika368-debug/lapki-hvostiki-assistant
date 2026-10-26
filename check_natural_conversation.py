"""Opt-in Gemini conversation check; persistence and Telegram are replaced by mocks."""
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock
from dotenv import load_dotenv
from assistant import Assistant, BookingState
from gemini_client import GeminiExtractor
from lapka import LapkaResponder, safe_text


class TracingResponder(LapkaResponder):
    accepted = False

    def compose(self, message, history, data):
        text = super().compose(message, history, data)
        self.accepted = safe_text(text, data)
        return text


def main():
    if os.getenv('RUN_NATURAL_CHECK') != '1':
        raise SystemExit('Set RUN_NATURAL_CHECK=1 for synthetic Gemini messages.')
    root = Path(__file__).parent
    load_dotenv(root / '.env')
    responder, store, notifier = TracingResponder(), Mock(), Mock()
    bot = Assistant(GeminiExtractor(), store, notifier=notifier, responder=responder)
    history, state, report = [], BookingState(), []
    for message in ['Что ты умеешь?',
                    'У меня кот Плюш, он очень боится. Кто сможет с ним поработать?',
                    'А что входит в комплекс для кошки?',
                    'Спасибо, я пока просто узнаю']:
        state.last_request = 0
        started = time.monotonic()
        responder.accepted = False
        history, state = bot.respond(message, history, state)
        row = {'message': message, 'answer': history[-1]['content'],
               'generated_reply_used': responder.accepted,
               'seconds': round(time.monotonic() - started, 2), 'error': state.last_error}
        report.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        assert not state.saved
    assert state.draft.pet_name == 'Плюш'
    passed = all(not case['error'] and case['generated_reply_used'] for case in report)
    store.save.assert_not_called()
    notifier.send.assert_not_called()
    (root / 'reports' / 'natural-conversation.json').write_text(json.dumps(
        {'result': 'passed' if passed else 'incomplete', 'model': os.getenv('GEMINI_MODEL', 'gemini-3.1-flash-lite'), 'cases': report, 'database_writes': 0, 'telegram_sends': 0}, ensure_ascii=False, indent=2))
    assert passed, 'See report: API error or rejected wording'


if __name__ == '__main__':
    main()
