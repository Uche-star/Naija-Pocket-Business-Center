import tkinter as tk
import sqlite3

from welcome_page import WelcomePage


APP_NAME = "Naija Pocket Business Center 2.0"


class NaijaPocketBusinessCenter:

    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("800x900")
        self.root.configure(bg="#0b0b0b")

        self.setup_database()
        self.show_welcome()

    def setup_database(self):
        self.conn = sqlite3.connect("naija_pocket.db")
        self.cursor = self.conn.cursor()

        self.cursor.execute("""
        CREATE TABLE IF NOT EXISTS customers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            service TEXT
        )
        """)

        self.conn.commit()

    def clear(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def label(self, text, size=12, bold=False, color="white"):
        return tk.Label(
            self.root,
            text=text,
            font=("Arial", size, "bold" if bold else "normal"),
            fg=color,
            bg="#0b0b0b"
        )

    def show_welcome(self):
        self.clear()
        WelcomePage(self)

    def open_ada(self):
        self.clear()
        from ada_conversation import AdaConversation
        AdaConversation(self.root, self)

    def open_nora(self):
        self.clear()
        try:
            from nora_conversation import NoraConversation
            NoraConversation(self.root, self)
        except ImportError:
            self.label(
                "Nora will be connected soon.",
                18,
                True
            ).pack(pady=40)

            tk.Button(
                self.root,
                text="Back",
                command=self.show_welcome,
                bg="#444444",
                fg="white",
                font=("Arial", 11, "bold")
            ).pack(pady=20)

    # Compatibility with your existing welcome_page.py
    def welcome_page(self):
        self.show_welcome()

    def chat_page(self, person):
        if person == "Ada":
            self.open_ada()
        elif person == "Nora":
            self.open_nora()


if __name__ == "__main__":
    root = tk.Tk()
    app = NaijaPocketBusinessCenter(root)
    root.mainloop() 
