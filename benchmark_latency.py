"""Opt-in latency measurement with real Gemini and no persistence operations."""
import json
import os
from pathlib import Path
import time
from unittest.mock import Mock

from dotenv import load_dotenv

from assistant import Assistant,BookingState
from gemini_client import GeminiExtractor
from lapka import LapkaResponder


class TimedExtractor(GeminiExtractor):
    def __init__(self):
        self.calls=0
        self.http_attempts=0
        self.failure=None
    def extract(self,*args,**kwargs):
        self.calls+=1
        try:
            result=super().extract(*args,**kwargs)
            self.http_attempts+=result._api_attempts
            return result
        except Exception as exc:
            self.failure={'type':type(exc).__name__,'code':getattr(exc,'code',None)}
            raise


def main():
    if os.getenv('RUN_LAPKA_BENCHMARK')!='1':
        raise SystemExit('Set RUN_LAPKA_BENCHMARK=1 for synthetic real Gemini timing.')
    load_dotenv(Path(__file__).with_name('.env'))
    records=[]
    for message in ['привет','спасибо','ты так долго отвечаешь','Сколько стоит комплекс для кошки и сколько длится?']:
        extractor,store,notifier=TimedExtractor(),Mock(),Mock()
        bot=Assistant(extractor,store,notifier=notifier,responder=LapkaResponder())
        started=time.perf_counter()
        h,state=bot.respond(message,[],BookingState())
        row={'message':message,'total_seconds':round(time.perf_counter()-started,4),
             'gemini_calls':extractor.calls,'answer':h[-1]['content'],'error':state.last_error}
        row['http_attempts']=extractor.http_attempts
        row['api_failure']=extractor.failure
        records.append(row)
        Path('reports/latency-after.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
        print(json.dumps(row,ensure_ascii=False),flush=True)
        assert not state.last_error
        assert extractor.calls==1
        if message.startswith('Сколько'):
            assert '2 500' in row['answer'] and 'администратор' in row['answer'].lower()
        store.save.assert_not_called();notifier.send.assert_not_called()


if __name__=='__main__':main()
