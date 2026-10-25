"""Opt-in real Gemini smoke test. Uses fake storage/notifier; never writes to Supabase."""
import json
import os
from pathlib import Path
from unittest.mock import Mock
from dotenv import load_dotenv
from assistant import Assistant, BookingState
from gemini_client import GeminiExtractor


def main():
    if os.getenv('RUN_GEMINI_SMOKE') != '1':
        raise SystemExit('Set RUN_GEMINI_SMOKE=1 to use Google Gemini with synthetic messages.')
    load_dotenv(Path(__file__).with_name('.env'))
    bot = Assistant(GeminiExtractor(), Mock(), notifier=Mock())
    report = []

    def turn(message, state=None, history=None):
        state = state or BookingState()
        state.last_request = 0
        history, state = bot.respond(message, history or [], state)
        report.append({'message':message, 'answer':history[-1]['content'],
                       'draft':state.draft.model_dump(), 'pending':bool(state.pending), 'issues':state.issues.copy()})
        Path('reports').mkdir(exist_ok=True)
        Path('reports/gemini-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return history,state

    h,s=turn('Корги, 12 кг, нужен комплекс в субботу после обеда')
    assert s.draft.pet_weight and '12' in s.draft.pet_weight and s.draft.service is None
    assert s.awaiting_field=='phone'
    h,s=turn('Я не хочу записываться. Только узнать цену и длительность комплекса для корги.',s,h)
    assert not s.active and '3 200' in h[-1]['content'] and 'Длительность' in h[-1]['content']
    assert 'Какую услугу' not in h[-1]['content'] and 'Стрижка когтей' not in h[-1]['content']
    h,s=turn('Собака сильно линяет, не понимаю, нужна ли ей стрижка')
    assert 'экспресс-линьку' in h[-1]['content'] and not s.active
    assert len(h[-1]['content']) < 900 and 'Креативное окрашивание' not in h[-1]['content']
    h,s=turn('Сколько стоит комплекс для кошки и сколько длится?')
    assert '2 500' in h[-1]['content'] and 'Длительность' in h[-1]['content']
    h,s=turn('А по времени?',s,h)
    assert 'Длительность' in h[-1]['content'] and '2 500' not in h[-1]['content']
    h,s=turn('Хочу оставить заявку на комплекс для кошки. Телефон +7 999 123-45-67. Время 15:00, дату согласовать.')
    assert s.pending and not s.draft.owner_name and not s.draft.preferred_date
    h,s=turn('Поменяйте время с 15:00 на 25:99',s,h)
    assert not s.pending and 'preferred_time' in s.issues and '25:99' in h[-1]['content']
    h,s=turn('Оставить как было',s,h)
    assert s.pending
    bot.store.save.assert_not_called()
    bot.notifier.send.assert_not_called()
    Path('reports').mkdir(exist_ok=True)
    Path('reports/gemini-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f'{len(report)} real Gemini conversation turns checked; no database or Telegram writes.')


if __name__=='__main__':
    main()
