@echo off
call "%USERPROFILE%\anaconda3\Scripts\activate.bat" chatvoice
streamlit run "%USERPROFILE%\voice-web-chatbot\app.py"
