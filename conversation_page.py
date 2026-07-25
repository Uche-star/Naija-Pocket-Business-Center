import tkinter as tk
from tkinter import filedialog, messagebox

def assistant_page(self, person):
    self.clear_screen()

    title = tk.Label(
        self.root,
        text=person,
        font=("Arial", 24, "bold"),
        fg="white",
        bg="#0b0b0b"
    )
    title.pack(pady=15)

    frame = tk.Frame(self.root, bg="#171717")
    frame.pack(fill="both", expand=True, padx=30, pady=10)

    chat = tk.Text(
        frame,
        bg="#111111",
        fg="white",
        font=("Arial", 12),
        wrap="word",
        height=20
    )
    chat.pack(fill="both", expand=True, padx=15, pady=15)

    if person == "Ada":
        msg = (
            "Ada is preparing a reply...\n"
            "● ● ●\n\n"
            "Hello, I be Ada.\n\n"
            "No wahala. Snap your handwritten note or typed document, "
            "or upload am here. I go assist you type am, format am, "
            "and prepare am for printing or sending."
        )
    else:
        msg = (
            "Nora is preparing a reply...\n"
            "● ● ●\n\n"
            "Hello, I am Nora.\n\n"
            "Please upload or snap your handwritten note or typed document. "
            "I will assist with typing and formatting it into a professional document."
        )

    chat.insert("end", msg)
    chat.config(state="disabled")

    buttons = tk.Frame(self.root, bg="#0b0b0b")
    buttons.pack(pady=10)

    tk.Button(buttons, text="📎 Upload Document",
              command=self.upload_file, width=20).grid(row=0, column=0, padx=5, pady=5)

    tk.Button(buttons, text="🎤 Talk",
              width=20).grid(row=0, column=1, padx=5, pady=5)

    tk.Button(buttons, text="📷 Snap Document",
              width=20).grid(row=1, column=0, padx=5, pady=5)

    tk.Button(self.root, text="Back",
              command=self.welcome_page).pack(pady=10)


def upload_file(self):
    file = filedialog.askopenfilename()
    if file:
        messagebox.showinfo("Upload", "Document selected successfully.")

 
