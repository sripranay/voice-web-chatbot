import gradio as gr
def echo(x): return f"OK: {x}"
gr.Interface(echo, gr.Textbox(label="Say something"), "text").launch()
