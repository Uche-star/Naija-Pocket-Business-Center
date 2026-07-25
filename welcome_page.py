import tkinter as tk

class WelcomePage:

    def __init__(self, app):
        self.app = app
        self.build()

    def build(self):

        self.app.clear()

        # Business Name
        self.app.label(
            "NAIJA POCKET\nBUSINESS CENTER",
            24,
            True
        ).pack(pady=(20,5))

        # Signature
        self.app.label(
            "Faster Than Traditional Typing",
            15,
            True,
            "#00FF99"
        ).pack(pady=(0,20))

        # Ada Picture
        # Ada's illustration will appear here.

        self.app.label(
            "👋 Hello and welcome!",
            18,
            True
        ).pack(pady=(10,10))

        self.app.label(
            "I'm Ada, your AI Business Center Assistant.\n\n"
            "I'm here for you 24 hours a day,\n"
            "7 days a week to help you with your\n"
            "digital business center needs.\n\n"
            "Get your work done from anywhere in Nigeria,\n"
            "even from your remote village or without\n"
            "leaving the comfort of your home.\n\n"
            "You can chat with me in English or\n"
            "Nigerian Pidgin English—whichever\n"
            "you're most comfortable with.",
            13
        ).pack(pady=10)

        self.app.label(
            "I can help you with:\n\n"
            "• Handwritten notes to typed documents\n"
            "• Document typing\n"
            "• Research assistance\n"
            "• Assignment and project support\n"
            "• CV and resume preparation\n"
            "• Business documents\n"
            "• ...and many more you'll see as you tap the button below.",
            12,
            False,
            "#00D4FF"
        ).pack(pady=15)

        self.app.label(
            "Tap the button below to talk to Ada or chat with her.",
            13,
            True
        ).pack(pady=10)

        tk.Button(
            self.app.root,
            text="Talk to Ada",
            command=lambda: self.app.chat_page("Ada"),
            bg="#008F5A",
            fg="white",
            font=("Arial",13,"bold"),
            padx=20,
            pady=8
        ).pack(pady=20) 
