"""Server-only Telegram delivery; no credentials or customer details in logs."""
import os
import httpx
from conversation import FIELD_LABELS


def message_chunks(text, limit=3500):
    chunk, units = '', 0
    for char in text:
        size = len(char.encode('utf-16-le')) // 2
        if units + size > limit:
            yield chunk
            chunk, units = '', 0
        chunk += char
        units += size
    if chunk:
        yield chunk


class TelegramNotifier:
    @property
    def enabled(self):
        return bool(os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_CHAT_ID'))

    def send(self, application_id, application, salon):
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
        if not token or not chat_id:
            raise RuntimeError('Telegram is not configured')
        values = application.model_dump(mode='json')
        if application.service:
            values['service'] = salon['services'][application.service]['name']
        if application.preferred_date:
            values['preferred_date'] = application.preferred_date.strftime('%d.%m.%Y')
        if application.preferred_time:
            values['preferred_time'] = application.preferred_time.strftime('%H:%M')
        text = '🐾 Новая заявка — ' + salon['name'] + '\n\n'
        text += '\n'.join(f'{label}: {values[key]}' for key,label in FIELD_LABELS.items() if values.get(key))
        text += '\n\n' + (application.handoff or 'Дополнительный контекст не указан.')
        text += f'\n\nНомер: {application_id}\nПожелания клиента, а не подтверждённая запись.'
        try:
            message_id = None
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                for chunk in message_chunks(text):
                    response = client.post(f'https://api.telegram.org/bot{token}/sendMessage', json={
                        'chat_id':chat_id, 'text':chunk, 'link_preview_options':{'is_disabled':True}})
                    response.raise_for_status()
                    data = response.json()
                    if not data.get('ok') or not isinstance(data.get('result',{}).get('message_id'),int):
                        raise ValueError('Delivery unconfirmed')
                    message_id = data['result']['message_id']
            return message_id
        except Exception:
            # No ambiguous timeout retry: Telegram has no idempotency parameter.
            raise RuntimeError('Telegram delivery was not confirmed') from None
