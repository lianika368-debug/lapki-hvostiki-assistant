"""Gradio UI: history and booking are independent server-side session states."""
import os
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
from dotenv import load_dotenv
load_dotenv()
import gradio as gr
from fastapi import FastAPI
import uvicorn
from assistant import Assistant, BookingState, SALON
from gemini_client import GeminiExtractor
from storage import SupabaseStore

assistant = Assistant(GeminiExtractor(), SupabaseStore())
WELCOME = [{"role": "assistant", "content": "Здравствуйте! 🐾 Я ассистент «Лапки & Хвостики». Подскажу цены, расскажу об уходе перед визитом и помогу оставить заявку. Чем помочь?"}]

def send(message, history, booking):
    history, booking = assistant.respond(message or "", history or list(WELCOME), booking or BookingState())
    return "", history, history, booking

def clear():
    return "", list(WELCOME), list(WELCOME), BookingState()

CSS = """
.gradio-container {max-width: 940px !important; margin: auto;}
#hero {padding: 20px 0 12px;}
#hero h1 {font-size: 2.3rem; letter-spacing: -.04em;}
footer {display: none !important;}
"""
with gr.Blocks(title="Лапки & Хвостики · Ассистент", analytics_enabled=False) as demo:
    gr.Markdown(f"# 🐾 {SALON['name']}\n### Забота о питомце начинается с разговора\n{SALON['hours']}", elem_id="hero")
    chat = gr.Chatbot(value=WELCOME, label="Ассистент салона", height=470,
                      sanitize_html=True, render_markdown=False)
    # State components are held by Gradio on the server, never global conversation lists.
    history = gr.State(value=lambda: list(WELCOME), time_to_live=3600)
    booking = gr.State(value=lambda: BookingState(), time_to_live=3600)
    with gr.Row():
        message = gr.Textbox(label="Ваше сообщение", placeholder="Например: сколько стоит груминг кошки?", max_lines=5, scale=5)
        submit = gr.Button("Отправить", variant="primary", scale=1)
    reset = gr.Button("Очистить диалог")
    gr.Markdown("Заявка отправляется только после вашего подтверждения. Выбранное время — пожелание, а не подтверждённая запись.\n\nСообщения обрабатываются Google Gemini; подтверждённая заявка сохраняется в Supabase. Не указывайте лишние персональные данные. Очистка удаляет текущий диалог, но не уже отправленную заявку.\n\n" + f"📍 {SALON['address']} · ☎ {SALON['phone']} · Telegram: {SALON['telegram']}")
    event_args = dict(fn=send, inputs=[message, history, booking], outputs=[message, chat, history, booking],
                      concurrency_id="chat", concurrency_limit=1, api_name=False)
    submit.click(**event_args)
    message.submit(**event_args)
    reset.click(fn=clear, outputs=[message, chat, history, booking],
                concurrency_id="chat", concurrency_limit=1, api_name=False)

demo.queue(max_size=32)
server = FastAPI()
@server.get("/healthz")
def health():
    return {"status": "ok"}
server = gr.mount_gradio_app(server, demo, path="/", theme=gr.themes.Soft(primary_hue="teal"), css=CSS)

if __name__ == "__main__":
    uvicorn.run(server, host="0.0.0.0", port=int(os.getenv("PORT", "7860")), access_log=False)
