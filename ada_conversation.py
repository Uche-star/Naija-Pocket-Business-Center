"""
ada_conversation.py
Premium Ada Chat Interface
Naija Pocket Business Center
"""

import tkinter as tk

from ada_controller import AdaController
from ada_buttons import AdaButtons


class AdaConversation:

    def __init__(self, root, app=None):

        self.root = root
        self.main_app = app
        self.controller = AdaController()

        self.root.title(
            "Naija Pocket Business Center"
        )

        self.root.configure(
            bg="#0B0B0B"
        )

        self.build_page()

    def build_page(self):

        for widget in self.root.winfo_children():
            widget.destroy()

        # =========================
        # CHAT AREA
        # =========================

        chat_frame = tk.Frame(
            self.root,
            bg="#171717"
        )

        chat_frame.pack(
            padx=20,
            pady=20,
            fill="both",
            expand=True
        )

        self.chat_box = tk.Text(
            chat_frame,
            bg="#171717",
            fg="white",
            font=(
                "Arial",
                12
            ),
            wrap="word",
            relief="flat",
            padx=20,
            pady=20,
            state="disabled"
        )

        self.chat_box.pack(
            fill="both",
            expand=True
        )

        self.chat_box.tag_configure(
            "ada",
            foreground="#FFD700",
            font=(
                "Arial",
                12,
                "bold"
            )
        )

        self.chat_box.tag_configure(
            "user",
            foreground="#00FF99",
            font=(
                "Arial",
                12,
                "bold"
            )
        )

        self.show_welcome_message(
            "Hello 😊 I am Ada.\n\n"
            "Your AI Business Center Assistant.\n\n"
            "Here are more things I fit help you with so that we get started:\n\n"
            "✓ Document formatting\n"
            "✓ Document editing\n"
            "✓ Grammar correction\n"
            "✓ Translation\n"
            "✓ Document summarization\n"
            "✓ PDF conversion\n"
            "✓ Research assistance\n"
            "✓ Business proposals\n"
            "✓ Company profiles\n"
            "✓ Invoices and quotations\n"
            "✓ Meeting minutes\n"
            "✓ Seminar papers\n"
            "✓ AI writing assistance\n"
            "✓ Rewrite and improve documents\n"
            "✓ Explain difficult topics\n"
            "✓ Voice-to-text\n"
            "✓ Image-to-text\n\n"
            "Send your document or chat with me.\n\n"
            "If your document na paper, tap Snap Document to take am picture.\n\n"
            "If you prefer talking, tap Voice."
        )

        AdaButtons(
            self.root,
            self
        )

    def show_welcome_message(self, message):

        self.chat_box.config(
            state="normal"
        )

        self.chat_box.insert(
            tk.END,
            message + "\n\n"
        )

        self.chat_box.config(
            state="disabled"
        )

        self.chat_box.see(
            tk.END
        )

    def show_message(self, sender, message):

        self.chat_box.config(
            state="normal"
        )

        if sender == "Ada":

            self.chat_box.insert(
                tk.END,
                "Ada:\n",
                "ada"
            )

        else:

            self.chat_box.insert(
                tk.END,
                "You:\n",
                "user"
            )

        self.chat_box.insert(
            tk.END,
            message + "\n\n"
        )

        self.chat_box.config(
            state="disabled"
        )

        self.chat_box.see(
            tk.END
        )

    def send_message(self, message):

        message = message.strip()

        if not message:
            return

        self.show_message(
            "You",
            message
        )

        try:

            reply = self.controller.process_message(
                message
            )

        except Exception as error:

            reply = (
                "Sorry 😊\n\n"
                "Ada get small problem:\n"
                f"{error}"
            )

        self.show_message(
            "Ada",
            reply
        )

    def welcome_page(self):

        if self.main_app:
            self.main_app.welcome_page()


if __name__ == "__main__":

    root = tk.Tk()

    root.geometry(
        "800x900"
    )

    AdaConversation(
        root
    )

    root.mainloop() 
