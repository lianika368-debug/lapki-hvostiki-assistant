"""Salon chat UI; each visitor has independent server-side conversation state."""
import html
import os
from pathlib import Path
from functools import partial
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))
import gradio as gr
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from assistant import Assistant, BookingState, SALON, QUESTIONS, render_facts
from models import Extraction
from gemini_client import GeminiExtractor
from storage import SupabaseStore
from notifications import TelegramNotifier

ROOT = Path(__file__).parent
assistant = Assistant(GeminiExtractor(), SupabaseStore(), notifier=TelegramNotifier())
WELCOME = [{"role": "assistant", "content": "Здравствуйте! 🐾 Я ассистент «Лапки & Хвостики». Помогу выбрать услугу, подскажу стоимость и приму заявку. Начнём с вопроса или знакомства с вашим питомцем?"}]
QUICK_QUESTIONS = {
    "prices": ("Какие у вас услуги и цены?", ["services"]),
    "included": ("Что входит в комплексный груминг?", ["included"]),
    "preparation": ("Как подготовить питомца к визиту?", ["preparation"]),
}

def controls(state):
    """Only offer answers for the field currently being collected."""
    missing = [key for key in QUESTIONS if not getattr(state.draft, key)]
    next_field = missing[0] if state.active and not state.pending and missing else None
    return (gr.update(visible=bool(state.pending and not state.saved)),
            gr.update(visible=next_field == "preferred_groomer"),
            gr.update(visible=next_field == "comment"))

def result(history, booking):
    return "", history, history, booking, *controls(booking)

def send(message, history, booking):
    history, booking = assistant.respond(message or "", history or list(WELCOME), booking or BookingState())
    return result(history, booking)

def preset(message, history, booking):
    return send(message, history, booking)

def quick_answer(topic, history, booking):
    # Fixed FAQ buttons use the same salon facts without a paid model call.
    question, topics = QUICK_QUESTIONS[topic]
    booking = booking or BookingState()
    answer = render_facts(Extraction(intent="общее", topics=topics), SALON)
    history, booking = assistant._reply(question, answer, history or list(WELCOME), booking)
    return result(history, booking)

def clear():
    return result(list(WELCOME), BookingState())

CSS = (ROOT / "styles.css").read_text()
# Gradio scopes media-query selectors; root viewport rules belong in the page head.
ROOT_STYLE = """<style>
@media(max-width:850px) { body .gradio-container.gradio-container {padding:0 22px 12px !important;margin:12px auto !important;} }
@media(max-width:650px) { body .gradio-container.gradio-container {border-radius:0 !important;max-width:100% !important;margin:0 !important;padding:0 19px 10px !important;border-left:0 !important;border-right:0 !important;} }
</style>"""
def safe(value):
    return html.escape(str(value), quote=True)

PAW = '<svg viewBox="0 0 48 48" fill="currentColor" aria-hidden="true"><ellipse cx="12" cy="16" rx="5" ry="7" transform="rotate(-25 12 16)"/><ellipse cx="23" cy="10" rx="5" ry="7"/><ellipse cx="35" cy="15" rx="5" ry="7" transform="rotate(25 35 15)"/><ellipse cx="41" cy="27" rx="4" ry="6" transform="rotate(40 41 27)"/><path d="M11 35c0-6 8-16 14-16s14 10 14 16-7 7-14 4c-7 3-14 2-14-4z"/></svg>'
HEADER = f'''<header class="salon-nav"><a class="brand" href="#top"><span class="brand-paw">{PAW}</span><span>{safe(SALON['name'])}<small>ГРУМИНГ · ЕКАТЕРИНБУРГ</small></span></a><div class="nav-right"><span class="hours-dot">Ежедневно · 10:00–20:00</span><a href="https://t.me/lapki_hvostiki_ekb" target="_blank" rel="noopener noreferrer">Telegram ↗</a></div></header>'''
HERO = '''<section class="hero" id="top"><div class="hero-copy"><span class="eyebrow">ДЛЯ ТЕХ, У КОГО ЛАПКИ</span><h1>Красота для лапок.<br><span>Забота для хвостиков.</span></h1><p>Поможем выбрать уход и оставить заявку на груминг.<br class="desktop-break"> Просто расскажите нам о своём питомце.</p><a class="hero-cta" href="#booking">Давайте знакомиться <span>↗</span></a><div class="hero-tags"><span>♡ Для кошек и собак</span><span>По предварительной заявке</span></div></div><div class="hero-visual"><div class="photo-frame"><img src="/salon-assets/playful-corgi.webp" alt="Весёлый корги с шапочкой пены" width="1254" height="1254" fetchpriority="high"></div><span class="photo-sticker">Груминг<br>с заботой ♡</span><span class="hero-sparkle" aria-hidden="true">✳</span></div></section>'''
ASIDE = f'''<aside class="visit-card"><span class="eyebrow">ДО ВСТРЕЧИ В САЛОНЕ</span><h3>Заглядывайте<br>с хвостиком ♡</h3><div class="visit-item"><span>ГДЕ МЫ</span><p>{safe(SALON['address'])}</p></div><div class="visit-item"><span>КОГДА ЖДЁМ</span><p>{safe(SALON['hours'])}</p></div><a class="phone-link" href="tel:+79991234567">{safe(SALON['phone'])}</a><a class="telegram-link" href="https://t.me/lapki_hvostiki_ekb" target="_blank" rel="noopener noreferrer">Написать в Telegram ↗</a></aside><div class="steps-card"><span class="eyebrow">ЗАПИСАТЬСЯ ПРОСТО</span><div><b>01</b><p>Расскажите о питомце<br><small>и выберите услугу</small></p></div><div><b>02</b><p>Проверьте заявку<br><small>и подтвердите отправку</small></p></div><div><b>03</b><p>Уточните запись у салона<br><small>время в заявке — пожелание</small></p></div></div>'''

THEME = gr.themes.Soft(primary_hue="pink", secondary_hue="lime", neutral_hue="stone", radius_size="lg").set(
    body_background_fill="#fff5f8", body_background_fill_dark="#fff5f8",
    body_text_color="#354130", body_text_color_dark="#354130",
    block_background_fill="#ffffff", block_background_fill_dark="#ffffff",
    block_border_color="#ecece5", block_border_color_dark="#ecece5",
    block_label_text_color="#5e665b", block_label_text_color_dark="#5e665b",
    input_background_fill="#ffffff", input_background_fill_dark="#ffffff",
    input_border_color="#e6e8dd", input_border_color_dark="#e6e8dd",
    input_placeholder_color="#82867c", input_placeholder_color_dark="#82867c",
    button_primary_background_fill="#e94797", button_primary_background_fill_dark="#e94797",
    button_primary_background_fill_hover="#d63384", button_primary_background_fill_hover_dark="#d63384",
    button_primary_text_color="#ffffff", button_primary_text_color_dark="#ffffff",
    button_secondary_background_fill="#f7f8f2", button_secondary_background_fill_dark="#f7f8f2",
    button_secondary_text_color="#354130", button_secondary_text_color_dark="#354130",
    background_fill_primary="#ffffff", background_fill_primary_dark="#ffffff",
    background_fill_secondary="#f6f7f0", background_fill_secondary_dark="#f6f7f0",
)

with gr.Blocks(title="Лапки & Хвостики · Груминг с заботой", analytics_enabled=False) as demo:
    gr.HTML(HEADER)
    gr.HTML(HERO)
    with gr.Row(elem_id="booking", equal_height=False):
        with gr.Column(scale=3, min_width=320, elem_id="chat-panel"):
            gr.HTML('<div class="section-title"><span class="eyebrow">ВАШ АССИСТЕНТ</span><h2>Задайте вопрос или запишите питомца <span>✳</span></h2><p>Выберите быстрый вопрос или напишите свой.</p></div>')
            with gr.Row(elem_id="quick-questions"):
                book = gr.Button("🐾 Хочу записаться", elem_id="quick-book", min_width=145)
                prices = gr.Button("Услуги и цены", min_width=125)
                included = gr.Button("Что входит в комплекс?", min_width=160)
                preparation = gr.Button("Как подготовиться?", min_width=145)
            chat = gr.Chatbot(value=WELCOME, label="Чат с ассистентом", show_label=False,
                              height=390, sanitize_html=True, render_markdown=False,
                              buttons=["copy"], elem_id="salon-chat")
            # State never leaves the server and is separate for each visitor.
            history = gr.State(value=lambda: list(WELCOME), time_to_live=3600)
            booking = gr.State(value=lambda: BookingState(), time_to_live=3600)
            confirm = gr.Button("✓ Подтвердить и отправить заявку", variant="primary", visible=False, elem_id="confirm-application")
            groomer_answer = gr.Button("Мастер — без предпочтений", visible=False, elem_classes=["context-answer"])
            comment_answer = gr.Button("Нет особенностей", visible=False, elem_classes=["context-answer"])
            with gr.Column(elem_id="message-card"):
                gr.HTML('<div class="composer-title"><span aria-hidden="true">✎</span> Ваше сообщение</div><p class="composer-description">Напишите вопрос или расскажите о питомце для записи.</p>')
                with gr.Row(elem_id="composer"):
                    message = gr.Textbox(label="Ваше сообщение", show_label=False, placeholder="Напишите здесь…", lines=2, max_lines=5, scale=5)
                    submit = gr.Button("Отправить ↗", variant="primary", scale=1, min_width=130)
            with gr.Row(elem_id="chat-bottom"):
                gr.HTML('<p class="chat-hint">♡ Сначала проверяем заявку. Отправляем — с вашего согласия.</p>')
                reset = gr.Button("Очистить диалог", size="sm", scale=0, min_width=145)
        with gr.Column(scale=1, min_width=260, elem_id="visit-panel"):
            gr.HTML(ASIDE)
    with gr.Accordion("О заявках и обработке данных", open=False, elem_id="privacy"):
        gr.Markdown("Выбранное время — пожелание, а не подтверждённая запись. Заявка сохраняется только после вашего подтверждения.\n\nСообщения обрабатываются Google Gemini; подтверждённая заявка сохраняется в Supabase и отправляется администратору салона в Telegram. Не указывайте лишние персональные данные. Очистка удаляет текущий диалог, но не уже отправленную заявку.")
    gr.HTML(f'<footer class="salon-footer"><span>{safe(SALON["name"])} <span class="footer-heart">♡</span></span><span>Для тех, кого мы любим.</span></footer>')
    outputs = [message, chat, history, booking, confirm, groomer_answer, comment_answer]
    common = dict(inputs=[history, booking], outputs=outputs, concurrency_id="chat", concurrency_limit=1, api_name=False)
    submit.click(fn=send, **{**common, "inputs": [message, history, booking]})
    message.submit(fn=send, **{**common, "inputs": [message, history, booking]})
    book.click(fn=partial(preset, "Хочу записаться"), **common)
    prices.click(fn=partial(quick_answer, "prices"), **common)
    included.click(fn=partial(quick_answer, "included"), **common)
    preparation.click(fn=partial(quick_answer, "preparation"), **common)
    confirm.click(fn=partial(preset, "Подтверждаю"), **common)
    groomer_answer.click(fn=partial(preset, "Предпочтительного мастера нет, без предпочтений"), **common)
    comment_answer.click(fn=partial(preset, "Нет особенностей"), **common)
    chat.clear(fn=clear, **{**common, "inputs": []})
    reset.click(fn=clear, **{**common, "inputs": []})

demo.queue(max_size=32)
server = FastAPI()
@server.get("/healthz")
def health():
    return {"status": "ok"}
server.mount("/salon-assets", StaticFiles(directory=ROOT / "assets"), name="assets")
server = gr.mount_gradio_app(server, demo, path="/", show_error=False, enable_monitoring=False,
                             blocked_paths=[str(ROOT / ".env"), str(ROOT / ".git")], theme=THEME, css=CSS, head=ROOT_STYLE)
if __name__ == "__main__":
    uvicorn.run(server, host="0.0.0.0", port=int(os.getenv("PORT", "7860")), access_log=False)
