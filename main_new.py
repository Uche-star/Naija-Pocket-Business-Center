import tkinter as tk


class WelcomePage:

    def __init__(self, app):
        self.app = app
        self.build()


    def build(self):

        self.app.clear()


        self.app.label(
            "NAIJA POCKET\nBUSINESS CENTER",
            24,
            True
        ).pack(pady=15)


        self.app.label(
            "Faster than traditional typing\n\n"
            "No queue • No closing hours\n\n"
            "Documents ready in minutes",
            14
        ).pack(pady=8)


        self.app.label(
            "Now you can work even from your village",
            13,
            False,
            "#00ff99"
        ).pack(pady=8)


        self.app.label(
            "Quality service\n"
            "Affordable pricing\n"
            "Designed for everyone",
            13,
            False,
            "#C9A227"
        ).pack(pady=8)


        self.app.label(
            "🌙 NIGHT SERVICE AVAILABLE\n\n"
            "Submit your documents anytime at night\n\n"
            "Your payment will be confirmed in the morning\n\n"
            "Collect your completed document\n\n"
            "No morning rush • No queueing • No stress",
            11,
            False,
            "#C9A227"
        ).pack(pady=10)



        cards = tk.Frame(
            self.app.root,
            bg="#0b0b0b"
        )

        cards.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=10
        )


        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)



        # ADA CARD

        ada = tk.Frame(
            cards,
            bg="#171717",
            padx=20,
            pady=15
        )

        ada.grid(
            row=0,
            column=0,
            padx=10,
            sticky="nsew"
        )


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
            "Pidgin English",
            fg="#cccccc",
            bg="#171717",
            font=("Arial",11),
            justify="center"
        ).pack()


        tk.Button(
            ada,
            text="Press here to talk to Ada",
            command=lambda:self.app.chat_page("Ada"),
            bg="#008f5a",
            fg="white",
            font=("Arial",11,"bold")
        ).pack(pady=10)



        # NORA CARD

        nora = tk.Frame(
            cards,
            bg="#171717",
            padx=20,
            pady=15
        )

        nora.grid(
            row=0,
            column=1,
            padx=10,
            sticky="nsew"
        )


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
            "English",
            fg="#cccccc",
            bg="#171717",
            font=("Arial",11),
            justify="center"
        ).pack()


        tk.Button(
            nora,
            text="Press here to talk to Nora",
            command=lambda:self.app.chat_page("Nora"),
            bg="#0055aa",
            fg="white",
            font=("Arial",11,"bold")
        ).pack(pady=10)


 
