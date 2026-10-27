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
from assistant import Assistant, BookingState, SALON
from ui_summary import render_summary
from conversation import render_facts
from models import Draft, Extraction
from quick_booking import BATCH_PROMPT, QuickBooking, is_booking_start, booking_details, form_values, form_patch, form_error
from gemini_client import GeminiExtractor
from lapka import LapkaResponder
from ui_gate import run_session, session_slot
from storage import SupabaseStore
from notifications import TelegramNotifier

ROOT = Path(__file__).parent
assistant = Assistant(GeminiExtractor(), SupabaseStore(), notifier=TelegramNotifier(), responder=LapkaResponder())
WELCOME = [{'role':'assistant','content':'Здравствуйте! Я Лапка, ассистент салона «Лапки & Хвостики». Помогу выбрать уход, узнать стоимость и подготовить заявку. Что нужно вашему питомцу?'}]
TYPING_MESSAGE = 'Лапка готовит ответ'
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
    review = render_summary(booking.pending, SALON) if ready else ''
    show_form = bool(booking.active and not booking.saved and not ready)
    values = form_values(booking.draft, SALON)
    refresh = not booking.form_rendered or (show_form and not booking.form_visible)
    fields = [gr.update(value=value) if refresh or booking.form_rendered.get(key) != value else gr.skip()
              for key, value in values.items()]
    booking.form_visible, booking.form_rendered = show_form, values
    status = ('Не удалось завершить обработку. Ввод сохранён для повтора; новая заявка не отправлена.' if booking.last_error else
              'Тестовая заявка сохранена. Это не подтверждённая запись в салон.' if booking.saved else
              'Заявка ещё не отправлена.' if ready else
              'Черновик заявки ещё не отправлен.' if booking.active else
              '')
    display_history = [dict(item) for item in history]
    for item in display_history:
        if item['role'] == 'assistant' and 'Проверьте заявку:' in item['content']:
            prefix = item['content'].split('Проверьте заявку:',1)[0]
            item['content'] = prefix + 'Данные готовы. Проверьте их ниже и нажмите «Отправить заявку».'
        elif item['role'] == 'assistant' and item['content'] == 'Что изменить в заявке? Можно назвать несколько изменений одним сообщением.':
            item['content'] = 'Измените нужные поля в форме ниже и нажмите «Проверить заявку».'
    return (input_value if booking.last_error else '', display_history, history, booking,
            gr.update(visible=ready), gr.update(visible=bool(booking.issues)),
            gr.update(visible=bool(booking.suggestion == 'shedding' and not booking.draft.service)),
            review, booking.revision or '',
            '<p role="status" aria-live="polite">' + status + '</p>',
            gr.update(visible=not len(history) > 1), gr.update(visible=booking.reset_requested),
            gr.update(visible=not ready and not show_form, value='Новая заявка' if booking.saved else 'Подготовить заявку'),
            gr.update(visible=show_form), *fields,
            '<p role="alert">' + html.escape(booking.form_error or (next(iter(booking.issues.values())) if booking.issues and show_form else '')) + '</p>')


def _send(message, history, booking, revision=None):
    if is_booking_start(message or ''):
        completed = list(_start_application(history, booking, message))
        completed[0] = ''
        return tuple(completed)
    history, booking = assistant.respond(message or '', history or list(WELCOME), booking or BookingState(), revision=revision or None)
    return result(history, booking, message)


def preset(message, history, booking, revision=None, request: gr.Request = None):
    yield from send_message(message, history, booking, revision, request=request)


def _start_application(history, booking, message='Хочу оставить заявку'):
    booking = booking or BookingState()
    if booking.saved:
        booking = BookingState(draft=Draft(owner_name=booking.draft.owner_name, phone=booking.draft.phone))
    booking.active, booking.last_error, booking.form_error = True, False, ''
    seed = booking_details(message, booking.draft, SALON)
    if seed:
        booking.pending, booking.revision = None, None
    complete = booking.draft.phone and (booking.draft.service or booking.draft.request_text)
    if complete and not booking.editing and not seed:
        answer = assistant._prepare(booking)
    else:
        booking.awaiting_field = 'request_text' if not (booking.draft.service or booking.draft.request_text) else 'phone'
        answer = BATCH_PROMPT
    history, booking = assistant._reply(message, answer, history or list(WELCOME), booking)
    completed = list(result(history, booking))
    if seed:
        for index, key in enumerate(('phone', 'task', 'when', 'note'), 14):
            completed[index] = gr.update(value=seed[key])
    completed[0] = gr.skip()  # Starting a draft must not erase an unsent message.
    return tuple(completed)


def start_application(history, booking, request: gr.Request = None):
    return run_session(request, lambda: _start_application(history, booking), _busy_result)


def _prepare_form(phone, task, when, note, history, booking):
    booking = booking or BookingState()
    if booking.saved:
        return result(history or list(WELCOME), booking)
    booking.active, booking.last_error = True, False
    raw = [phone or '', task or '', when or '', note or '']
    try:
        data = QuickBooking(phone=raw[0], task=raw[1], when=raw[2], note=raw[3])
        patch = form_patch(data, booking.draft, SALON)
    except ValueError as exc:
        booking.pending, booking.revision = None, None
        booking.form_error = form_error(exc)
        completed = list(result(history or list(WELCOME), booking))
        for index, value, limit in zip(range(14, 18), raw, (30, 1800, 300, 1000)):
            completed[index] = gr.update(value=value[:limit])
        completed[0] = gr.skip()
        return tuple(completed)
    if any(getattr(booking.draft, key) != value for key, value in patch.items()):
        assistant._invalidate(booking)
    for key, value in patch.items():
        setattr(booking.draft, key, value)
    for key in ('phone', 'request_text', 'service', 'comment', 'preferred_date', 'preferred_time', 'schedule_note', 'edit'):
        booking.issues.pop(key, None)
    booking.form_error, booking.editing = '', False
    answer = assistant._prepare(booking)
    if not booking.pending:
        booking.form_error = answer
    history, booking = assistant._reply('Данные формы заявки', answer, history or list(WELCOME), booking)
    completed = list(result(history, booking))
    completed[0] = gr.skip()
    return tuple(completed)


def prepare_form(phone, task, when, note, history, booking, request: gr.Request = None):
    return run_session(request, lambda: _prepare_form(phone, task, when, note, history, booking), _busy_result)


def _quick_answer(topic, history, booking):
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


def _clear():
    return result(list(WELCOME), BookingState())


def _request_reset(history, booking):
    booking = booking or BookingState()
    booking.reset_requested = True
    return result(history or list(WELCOME), booking)


def _cancel_reset(history, booking):
    booking.reset_requested = False
    return result(history, booking)


def _choose_shedding(history, booking):
    if booking.saved:
        booking = BookingState()
    booking.draft.service = 'shedding'
    booking.draft.request_text = 'Экспресс-линька'
    booking.suggestion = None
    assistant._invalidate(booking)
    reply = assistant._prepare(booking) if booking.active else 'Выбрали экспресс-линьку: от 1 500 ₽. Что ещё хотите уточнить? Если готовы передать запрос администратору, нажмите «Подготовить заявку».'
    history, booking = assistant._reply('Выбираю экспресс-линьку', reply, history, booking)
    return result(history, booking)


def _busy_result():
    updates = [gr.skip() for _ in outputs]
    updates[9] = '<p role="status" aria-live="polite">Предыдущее сообщение ещё обрабатывается. Дождитесь ответа.</p>'
    return tuple(updates)


def send(message, history, booking, revision=None, request: gr.Request = None):
    return run_session(request, lambda: _send(message, history, booking, revision), _busy_result)


def send_message(message, history, booking, revision=None, request: gr.Request = None):
    """Clear accepted input before Gemini runs; hold the session lock through both updates."""
    with session_slot(request) as acquired:
        if not acquired:
            yield _busy_result()
            return
        if not (message or '').strip():
            yield result(history or list(WELCOME), booking or BookingState())
            return
        if is_booking_start(message):
            completed = list(_send(message, history, booking, revision))
            completed[0] = gr.update(value='', interactive=True)
            yield tuple(completed)
            return
        processing = [gr.skip() for _ in outputs]
        processing[0] = gr.update(value='', interactive=False)
        processing[1] = result(history or list(WELCOME), booking or BookingState())[1] + [{'role':'user', 'content':message}, {'role':'assistant', 'content':TYPING_MESSAGE}]
        processing[9] = '<p role="status" aria-live="polite" data-thinking="true">Сообщение отправлено. Лапка готовит ответ…</p>'
        yield tuple(processing)
        try:
            completed = list(_send(message, history, booking, revision))
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('chat_ui_failed error_type=%s', type(exc).__name__)
            completed = [gr.skip() for _ in outputs]
            completed[0] = message
            completed[1] = [*(history or list(WELCOME)), {'role':'user', 'content':message},
                            {'role':'assistant', 'content':'Не удалось завершить обработку. Повторите сообщение, пожалуйста.'}]
            completed[2] = completed[1]
            completed[9] = '<p role="status" aria-live="polite">Не удалось завершить обработку. Ввод восстановлен: повторите сообщение.</p>'
        completed[0] = gr.update(value=completed[0], interactive=True)
        yield tuple(completed)


def quick_answer(topic, history, booking, request: gr.Request = None):
    return run_session(request, lambda: _quick_answer(topic, history, booking), _busy_result)


def clear(request: gr.Request = None):
    return run_session(request, _clear, _busy_result)


def request_reset(history, booking, request: gr.Request = None):
    return run_session(request, lambda: _request_reset(history, booking), _busy_result)


def cancel_reset(history, booking, request: gr.Request = None):
    return run_session(request, lambda: _cancel_reset(history, booking), _busy_result)


def choose_shedding(history, booking, request: gr.Request = None):
    return run_session(request, lambda: _choose_shedding(history, booking), _busy_result)


THEME = gr.themes.Soft(primary_hue='pink', secondary_hue='lime', neutral_hue='stone',
                       font=['Arial', 'Helvetica', 'sans-serif']).set(
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

with gr.Blocks(title='Лапки & Хвостики — AI-ассистент', analytics_enabled=False) as demo:
    gr.HTML('<header class="salon-nav"><div class="salon-brand"><svg class="brand-paw" viewBox="0 0 40 40" aria-hidden="true"><ellipse cx="9" cy="15" rx="4.5" ry="6" transform="rotate(-25 9 15)"/><ellipse cx="18" cy="9" rx="4.5" ry="6" transform="rotate(-8 18 9)"/><ellipse cx="28" cy="11" rx="4.5" ry="6" transform="rotate(20 28 11)"/><ellipse cx="34" cy="20" rx="4" ry="5.5" transform="rotate(35 34 20)"/><path d="M11 29c0-5 5-13 10-13s12 8 12 13c0 7-7 4-11 4s-11 3-11-4Z"/></svg><div><strong>Лапки &amp; Хвостики</strong><div class="brand-meta"><span class="salon-location">Груминг · Екатеринбург</span><span class="demo-badge">Демо · учебный кейс</span></div></div></div><a href="https://t.me/lapki_hvostiki_ekb" target="_blank" rel="noopener noreferrer" aria-label="Написать салону в Telegram, откроется в новой вкладке"><span>Написать салону</span><small>в Telegram <span aria-hidden="true">↗</span></small></a></header>')
    gr.HTML('<section class="salon-hero" aria-label="Забота о питомцах"><img src="/salon-assets/playful-corgi.webp" alt="Весёлый корги" width="144" height="144"><p class="hero-title"><span>Чистые лапки.</span><span>Счастливые хвостики.</span></p></section>')
    gr.HTML('<div class="intro"><h1>Задайте вопрос или подготовьте заявку</h1></div>')
    history = gr.State(value=lambda: list(WELCOME), time_to_live=3600)
    booking = gr.State(value=lambda: BookingState(), time_to_live=3600)
    revision = gr.Textbox(visible=False, value='')
    with gr.Row(elem_id='quick-questions') as starters:
        prices = gr.Button('Цены', min_width=0)
        included = gr.Button('Что входит', min_width=0)
        preparation = gr.Button('Подготовка', min_width=0)
    chat = gr.Chatbot(value=WELCOME, label='Переписка с ассистентом', show_label=False,
                      height=170, min_height=150, autoscroll=True,
                      sanitize_html=True, render_markdown=False, allow_tags=False,
                      buttons=[], elem_id='salon-chat')
    with gr.Column(elem_id='message-card'):
        with gr.Row(elem_id='composer'):
            message = gr.Textbox(label='Ваше сообщение', show_label=False,
                                 placeholder='Напишите…', lines=1, max_lines=5,
                                 max_length=4000, scale=5, min_width=120,
                                 html_attributes={'enterkeyhint':'send'})
            submit = gr.Button('Отправить', variant='secondary', scale=1, min_width=100)
        gr.HTML('<p class="input-hint">Enter — отправить · Shift+Enter — новая строка</p>')
    gr.HTML('<p class="demo-caption">Демо-версия: заявки тестовые, используйте вымышленные данные</p>')
    status = gr.HTML('<p role="status" aria-live="polite"></p>', elem_id='conversation-status')
    with gr.Row(elem_id='context-actions'):
        book = gr.Button('Подготовить заявку', variant='primary', size='sm', elem_id='start-application')
        keep = gr.Button('Оставить как было', visible=False, size='sm')
        select_service = gr.Button('Выбрать экспресс-линьку', visible=False, size='sm')
    with gr.Column(visible=False, elem_id='booking-form') as booking_form:
        gr.HTML('<h2>Быстрая заявка</h2><p class="booking-hint">Заполните два обязательных поля. Пожелания можно указать ниже.</p>')
        with gr.Row():
            form_phone = gr.Textbox(label='Телефон *', placeholder='+7 (999) 123-45-67', max_length=30, scale=1, min_width=220)
            form_task = gr.Textbox(label='Услуга или что нужно питомцу *', placeholder='Например, стрижка когтей или помощь с линькой', max_length=1800, scale=2, min_width=240)
        form_when = gr.Textbox(visible=False, max_length=300)
        form_note = gr.Textbox(label='Пожелания (необязательно)', placeholder='Например, удобное время или особенности питомца', lines=2, max_length=1000)
        form_errors = gr.HTML('<p role="alert"></p>', elem_id='booking-form-error')
        with gr.Row():
            form_next = gr.Button('Проверить заявку', variant='primary')
            form_cancel = gr.Button('Отменить заявку', variant='secondary')
    with gr.Column(visible=False, elem_id='review-panel') as review_panel:
        gr.HTML('<h2>Тестовая заявка</h2>')
        review = gr.HTML('', elem_id='application-summary')
        with gr.Row():
            confirm = gr.Button('Отправить заявку', variant='primary')
            edit = gr.Button('Изменить')
            cancel = gr.Button('Отменить')
    reset = gr.Button('Начать заново', size='sm', elem_id='reset-chat')
    with gr.Group(visible=False, elem_id='reset-confirm') as reset_panel:
        gr.HTML('<p>Очистить переписку и черновик? Уже отправленная заявка останется у салона.</p>')
        with gr.Row():
            reset_yes = gr.Button('Да, очистить')
            reset_no = gr.Button('Продолжить разговор')
    gr.HTML('<details class="data-disclosure"><summary><span>🔒 Данные и контакты</span>'
            '<svg class="disclosure-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></summary>'
            '<div class="data-content"><p>Тестовые заявки после подтверждения действительно сохраняются в Supabase и отправляются в Telegram.</p>'
            f'<p>{html.escape(SALON["address"])}</p><p>{html.escape(SALON["hours"])}</p>'
            f'<p>Телефон: {html.escape(SALON["phone"])}. Telegram салона: {html.escape(SALON["telegram"])}.</p>'
            '<p>Черновик и переписка хранятся только в текущей сессии до 1 часа. При обновлении страницы, закрытии или перезапуске сервиса они могут исчезнуть. '
            'Отправка сама по себе при обновлении не повторяется.</p>'
            '<p>Сообщения обрабатывает Google Gemini. После явного подтверждения заявка сохраняется в Supabase и отправляется администратору в Telegram. '
            'Не указывайте лишних персональных данных. Очистка чата не удаляет уже отправленную заявку. '
            'Неизвестные длительность, категорию размера и возможность записи уточняет администратор.</p></div></details>', elem_id='privacy')
    outputs = [message, chat, history, booking, review_panel, keep, select_service, review, revision, status, starters, reset_panel, book, booking_form, form_phone, form_task, form_when, form_note, form_errors]
    common = dict(outputs=outputs, concurrency_id='chat', concurrency_limit=4, api_name=False,
                  trigger_mode='once', show_progress='minimal')
    submit.click(send_message, inputs=[message,history,booking,revision], **common)
    message.submit(send_message, inputs=[message,history,booking,revision], **common)
    book.click(start_application, inputs=[history,booking], **common)
    form_next.click(prepare_form, inputs=[form_phone,form_task,form_when,form_note,history,booking], **common)
    form_cancel.click(partial(preset,'Отменить заявку'), inputs=[history,booking,revision], **common)
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
