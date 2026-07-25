import tkinter as tk
from tkinter import messagebox
import sqlite3


APP_NAME = "Naija Pocket Business Center 2.0"


class NaijaPocketBusinessCenter:

    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("800x900")
        self.root.configure(bg="#0b0b0b")

        self.setup_database()
        self.welcome_page()


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


    def welcome_page(self):

        self.clear()

        self.label(
            "NAIJA POCKET\nBUSINESS CENTER",
            24,
            True
        ).pack(pady=20)


        self.label(
            "Faster than traditional typing.\n"
            "No queue. No closing hours.\n"
            "Documents ready in minutes.",
            14
        ).pack(pady=10)


        self.label(
            "Now you can work even from your village.",
            13,
            False,
            "#00ff99"
        ).pack(pady=10)


        ada = tk.Frame(
            self.root,
            bg="#171717",
            padx=20,
            pady=15
        )
        ada.pack(fill="x", padx=40, pady=10)


        tk.Label(
            ada,
            text="Meet Ada",
            font=("Arial",18,"bold"),
            fg="white",
            bg="#171717"
        ).pack()


        tk.Label(
            ada,
            text=
            "Your Business Center Assistant\n\n"
            "Ada will do your typing, printing,\n"
            "scanning, photocopying,\n"
            "document preparation and other\n"
            "business centre services.\n\n"
            "Ada understands English and\n"
            "Pidgin English.",
            fg="#cccccc",
            bg="#171717",
            font=("Arial",11)
        ).pack()


        tk.Button(
            ada,
            text="Press here to talk to Ada",
            command=lambda:self.chat_page("Ada"),
            bg="#008f5a",
            fg="white",
            font=("Arial",12,"bold")
        ).pack(pady=10)



        nora = tk.Frame(
            self.root,
            bg="#171717",
            padx=20,
            pady=15
        )
        nora.pack(fill="x", padx=40, pady=10)


        tk.Label(
            nora,
            text="Meet Nora",
            font=("Arial",18,"bold"),
            fg="white",
            bg="#171717"
        ).pack()


        tk.Label(
            nora,
            text=
            "Your Company Secretary &\n"
            "Personal Assistant\n\n"
            "Nora will help you prepare\n"
            "professional letters, reports,\n"
            "company documents, proposals\n"
            "and official correspondence.\n\n"
            "Nora understands Professional\n"
            "English.",
            fg="#cccccc",
            bg="#171717",
            font=("Arial",11)
        ).pack()


        tk.Button(
            nora,
            text="Press here to talk to Nora",
            command=lambda:self.chat_page("Nora"),
            bg="#0055aa",
            fg="white",
            font=("Arial",12,"bold")
        ).pack(pady=10)



    def chat_page(self, person):

        self.clear()


        self.label(
            f"{person}",
            24,
            True
        ).pack(pady=20)


        chat = tk.Text(
            self.root,
            height=20,
            width=70,
            bg="#171717",
            fg="white",
            font=("Arial",12)
        )
        chat.pack(padx=30,pady=10)


        if person == "Ada":

            chat.insert(
                "end",
                "Ada is preparing a reply...\n"
                "● ● ●\n\n"
                "No wahala. If you get handwritten note "
                "or typed document, just upload am or "
                "snap am here. I go assist you type am, "
                "format am, and prepare am for printing "
                "or sending.\n"
            )

        else:

            chat.insert(
                "end",
                "Nora is preparing a reply...\n"
                "● ● ●\n\n"
                "Please upload your handwritten note "
                "or typed document. I will assist with "
                "typing and formatting it into a properly "
                "prepared document for printing or delivery.\n"
            )


        buttons = tk.Frame(
            self.root,
            bg="#0b0b0b"
        )
        buttons.pack(pady=15)


        for text in [
            "📎 Upload Document",
            "🎤 Talk",
            "📷 Snap Document"
        ]:
            tk.Button(
                buttons,
                text=text,
                bg="#222222",
                fg="white",
                font=("Arial",11,"bold"),
                width=18
            ).pack(pady=5)


        tk.Button(
            self.root,
            text="Back",
            command=self.welcome_page,
            bg="#444444",
            fg="white"
        ).pack(pady=10)



if __name__ == "__main__":

    root = tk.Tk()

    app = NaijaPocketBusinessCenter(root)

    root.mainloop() 
