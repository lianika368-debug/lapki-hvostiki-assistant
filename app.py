"""Gradio chat with a separate, current review and session-only private data."""
import html
import os
from pathlib import Path
from functools import partial
os.environ.setdefault('GRADIO_ANALYTICS_ENABLED', 'False')
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name('.env'))
import gradio as gr
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from assistant import Assistant, BookingState, SALON, summary
from conversation import render_facts
from models import Extraction
from gemini_client import GeminiExtractor
from storage import SupabaseStore
from notifications import TelegramNotifier

ROOT = Path(__file__).parent
assistant = Assistant(GeminiExtractor(), SupabaseStore(), notifier=TelegramNotifier())
WELCOME = [{'role':'assistant','content':'Здравствуйте! Помогу выбрать уход, узнать стоимость и подготовить заявку. Что нужно вашему питомцу?'}]
QUICK_QUESTIONS = {
    'prices': ('Какие у вас услуги и цены?', ['services']),
    'included': ('Что входит в комплексный груминг?', ['included']),
    'preparation': ('Как подготовить питомца к визиту?', ['preparation']),
}


def controls(state):
    ready = bool(state.pending and not state.saved and not state.issues and not state.editing)
    return (gr.update(visible=ready), gr.update(visible=bool(state.issues)),
            gr.update(visible=bool(state.suggestion == 'shedding' and not state.draft.service)))


def result(history, booking, input_value=''):
    ready = bool(booking.pending and not booking.saved and not booking.issues and not booking.editing)
    review = '<pre>' + html.escape(summary(booking.pending, SALON)) + '</pre>' if ready else ''
    status = ('Не отправлено. Можно повторить: ввод сохранён.' if booking.last_error else
              'Заявка сохранена.' if booking.saved else
              'Проверьте актуальную заявку ниже.' if ready else 'Можно продолжать разговор.')
    display_history = [dict(item) for item in history]
    for item in display_history:
        if item['role'] == 'assistant' and 'Проверьте заявку:' in item['content']:
            prefix = item['content'].split('Проверьте заявку:',1)[0]
            item['content'] = prefix + 'Сводка заявки подготовлена. Проверьте актуальную версию в отдельном блоке ниже. Данные можно изменить или отменить.'
    return (input_value if booking.last_error else '', display_history, history, booking,
            gr.update(visible=ready), gr.update(visible=bool(booking.issues)),
            gr.update(visible=bool(booking.suggestion == 'shedding' and not booking.draft.service)),
            review, booking.revision or '',
            '<p role="status" aria-live="polite">' + status + '</p>',
            gr.update(visible=not len(history) > 1), gr.update(visible=booking.reset_requested))


def send(message, history, booking, revision=None):
    history, booking = assistant.respond(message or '', history or list(WELCOME), booking or BookingState(), revision=revision or None)
    return result(history, booking, message)


def preset(message, history, booking, revision=None):
    return send(message, history, booking, revision)


def quick_answer(topic, history, booking):
    question, topics = QUICK_QUESTIONS[topic]
    booking = booking or BookingState()
    booking.last_error = False
    for item in topics:
        if item not in booking.explained:
            booking.explained.append(item)
    answer = render_facts(Extraction(intent='общее', topics=topics), SALON, booking.draft)
    history, booking = assistant._reply(question, answer, history or list(WELCOME), booking)
    # Rebuild handoff too, so the administrator sees what has already been explained.
    if booking.pending and not booking.saved:
        assistant._prepare(booking)
    return result(history, booking)


def clear():
    return result(list(WELCOME), BookingState())


def request_reset(history, booking):
    booking = booking or BookingState()
    booking.reset_requested = True
    return result(history or list(WELCOME), booking)


def cancel_reset(history, booking):
    booking.reset_requested = False
    return result(history, booking)


def choose_shedding(history, booking):
    if booking.saved:
        booking = BookingState()
    booking.draft.service = 'shedding'
    booking.draft.request_text = 'Экспресс-линька'
    booking.suggestion = None
    assistant._invalidate(booking)
    reply = assistant._prepare(booking) if booking.active else 'Выбрали экспресс-линьку: от 1 500 ₽. Что ещё хотите уточнить? Если готовы передать запрос администратору, нажмите «Подготовить заявку».'
    history, booking = assistant._reply('Выбираю экспресс-линьку', reply, history, booking)
    return result(history, booking)


THEME = gr.themes.Soft(primary_hue='pink', secondary_hue='lime', neutral_hue='stone').set(
    body_background_fill='#fff5f8', body_background_fill_dark='#fff5f8',
    body_text_color='#354130', body_text_color_dark='#354130',
    block_background_fill='#ffffff', block_background_fill_dark='#ffffff',
    input_background_fill='#ffffff', input_background_fill_dark='#ffffff',
    button_primary_background_fill='#b52c70', button_primary_background_fill_dark='#b52c70',
    button_primary_text_color='#ffffff', button_primary_text_color_dark='#ffffff',
)
CSS = (ROOT / 'styles.css').read_text()
# Root sizing belongs outside Gradio's scoped stylesheet.
HEAD = '<style>body .gradio-container.gradio-container{width:100%!important;min-width:0!important;box-sizing:border-box!important;max-width:1100px!important;padding:0 22px 18px!important;margin:0 auto!important;}@media(max-width:650px){body .gradio-container.gradio-container{padding:0 12px 10px!important;}}</style>'
JS = (ROOT / 'ui.js').read_text()
I18N = gr.I18n(**{lang: {'common.complete':'Готово','common.loading':'Обработка…',
    'chatbot.clear':'Начать заново','chatbot.copy_message':'Скопировать сообщение'} for lang in ('ru','en')})

with gr.Blocks(title='Лапки & Хвостики — ассистент', analytics_enabled=False) as demo:
    gr.HTML('<header class="salon-nav"><div><strong>Лапки &amp; Хвостики</strong><span>Груминг · Екатеринбург</span></div><img src="/salon-assets/playful-corgi.webp" alt="Весёлый корги" width="64" height="64"></header>')
    gr.HTML('<div class="intro"><h1>Задайте вопрос или подготовьте заявку</h1><p>Поможем выбрать уход. Запись и точное время подтверждает администратор.</p></div>')
    history = gr.State(value=lambda: list(WELCOME), time_to_live=3600)
    booking = gr.State(value=lambda: BookingState(), time_to_live=3600)
    revision = gr.Textbox(visible=False, value='')
    with gr.Row(elem_id='quick-questions') as starters:
        prices = gr.Button('Услуги и цены', min_width=120)
        included = gr.Button('Что входит в комплекс?', min_width=180)
        preparation = gr.Button('Как подготовиться?', min_width=160)
    chat = gr.Chatbot(value=WELCOME, label='Переписка с ассистентом', show_label=False,
                      height='clamp(320px, 56svh, 620px)', min_height=280, autoscroll=True,
                      sanitize_html=True, render_markdown=False, allow_tags=False,
                      buttons=[], elem_id='salon-chat')
    status = gr.HTML('<p role="status" aria-live="polite">Можно задать любой вопрос об уходе.</p>', elem_id='conversation-status')
    with gr.Row(elem_id='context-actions'):
        book = gr.Button('Подготовить заявку', size='sm')
        keep = gr.Button('Оставить как было', visible=False, size='sm')
        select_service = gr.Button('Выбрать экспресс-линьку', visible=False, size='sm')
    with gr.Group(visible=False, elem_id='review-panel') as review_panel:
        gr.HTML('<h2>Актуальная заявка</h2>')
        review = gr.HTML('', elem_id='application-summary')
        with gr.Row():
            confirm = gr.Button('Отправить заявку', variant='primary')
            edit = gr.Button('Изменить')
            cancel = gr.Button('Отменить')
    with gr.Column(elem_id='message-card'):
        with gr.Row(elem_id='composer'):
            message = gr.Textbox(label='Ваше сообщение', show_label=False,
                                 placeholder='Напишите…', lines=1, max_lines=5,
                                 max_length=4000, scale=5, min_width=120,
                                 html_attributes={'enterkeyhint':'send'})
            submit = gr.Button('Отправить', variant='primary', scale=1, min_width=100)
        gr.HTML('<p class="input-hint">Enter — отправить · Shift+Enter — новая строка</p>')
    reset = gr.Button('Начать заново', size='sm', elem_id='reset-chat')
    with gr.Group(visible=False, elem_id='reset-confirm') as reset_panel:
        gr.HTML('<p>Очистить переписку и черновик? Уже отправленная заявка останется у салона.</p>')
        with gr.Row():
            reset_yes = gr.Button('Да, очистить')
            reset_no = gr.Button('Продолжить разговор')
    with gr.Accordion('Контакты, срок жизни черновика и обработка данных', open=False, elem_id='privacy'):
        gr.Markdown(f"{SALON['address']}\n\n{SALON['hours']}\n\nТелефон: {SALON['phone']}. Telegram: {SALON['telegram']}.\n\n"
            'Черновик и переписка хранятся только в текущей сессии до 1 часа. При обновлении страницы, закрытии или перезапуске сервиса они могут исчезнуть. '
            'Отправка сама по себе при обновлении не повторяется.\n\n'
            'Сообщения обрабатывает Google Gemini. После явного подтверждения заявка сохраняется в Supabase и отправляется администратору в Telegram. '
            'Не указывайте лишних персональных данных. Очистка чата не удаляет уже отправленную заявку. '
            'Неизвестные длительность, категорию размера и возможность записи уточняет администратор.')
    outputs = [message, chat, history, booking, review_panel, keep, select_service, review, revision, status, starters, reset_panel]
    common = dict(outputs=outputs, concurrency_id='chat', concurrency_limit=1, api_name=False,
                  trigger_mode='once', show_progress='minimal')
    submit.click(send, inputs=[message,history,booking,revision], **common)
    message.submit(send, inputs=[message,history,booking,revision], **common)
    book.click(partial(preset,'Хочу оставить заявку'), inputs=[history,booking,revision], **common)
    confirm.click(partial(preset,'Подтверждаю'), inputs=[history,booking,revision], **common)
    edit.click(partial(preset,'Изменить заявку'), inputs=[history,booking,revision], **common)
    cancel.click(partial(preset,'Отменить заявку'), inputs=[history,booking,revision], **common)
    keep.click(partial(preset,'Оставить как было'), inputs=[history,booking,revision], **common)
    select_service.click(choose_shedding, inputs=[history,booking], **common)
    for button, topic in ((prices,'prices'),(included,'included'),(preparation,'preparation')):
        button.click(partial(quick_answer,topic),inputs=[history,booking],**common)
    reset.click(request_reset, inputs=[history,booking], **common)
    reset_no.click(cancel_reset, inputs=[history,booking], **common)
    reset_yes.click(clear, inputs=[], **common)
    # Defensive handling if Gradio's own clear event is invoked directly.
    chat.clear(request_reset, inputs=[history,booking], **common)

demo.queue(max_size=32, api_open=False)
server = FastAPI()
@server.get('/healthz')
def health():
    return {'status':'ok'}
server.mount('/salon-assets', StaticFiles(directory=ROOT/'assets'), name='assets')
server = gr.mount_gradio_app(server,demo,path='/',show_error=False,enable_monitoring=False,
    blocked_paths=[str(ROOT/'.env'),str(ROOT/'.git')],theme=THEME,css=CSS,head=HEAD,js=JS,i18n=I18N)
if __name__ == '__main__':
    uvicorn.run(server,host='0.0.0.0',port=int(os.getenv('PORT','7860')),access_log=False)
