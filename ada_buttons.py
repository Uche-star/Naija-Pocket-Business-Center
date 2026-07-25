import tkinter as tk
from tkinter import filedialog, messagebox
import os


class AdaButtons:
    def __init__(self, parent, app=None):
        self.parent = parent
        self.app = app
        self.build_buttons()

    def upload_document(self):
        file_path = filedialog.askopenfilename(
            title="Select Document",
            filetypes=[
                ("Documents", "*.pdf *.doc *.docx *.txt"),
                ("Images", "*.jpg *.jpeg *.png"),
                ("All Files", "*.*")
            ]
        )

        if file_path:
            file_name = os.path.basename(file_path)

            if self.app:
                self.app.show_message(
                    "You",
                    f"Uploaded document: {file_name}"
                )

                self.app.show_message(
                    "Ada",
                    "I don receive your document.\n\n"
                    f"Document: {file_name}\n\n"
                    "Tell me wetin you want make I do with am.\n\n"
                    "• Type the document\n"
                    "• Correct grammar\n"
                    "• Arrange formatting\n"
                    "• Convert am to PDF\n"
                    "• Prepare am for printing\n"
                    "• Translate am\n"
                    "• Summarize am"
                )

    def talk_to_ada(self):
        if self.app:
            self.app.show_message(
                "Ada",
                "I dey listen.\n\n"
                "Wetin you want make I help you do?"
            )

    def snap_document(self):
        messagebox.showinfo(
            "Ada",
            "Camera connection go come in the next version."
        )

    def go_back(self):
        if self.app:
            self.app.welcome_page()

    def send_message(self):
        message = self.message_entry.get().strip()

        if not message:
            return

        self.message_entry.delete(0, tk.END)

        if self.app:
            self.app.send_message(message)

    def build_buttons(self):

        button_frame = tk.Frame(
            self.parent,
            bg="#0b0b0b"
        )

        button_frame.pack(
            pady=10
        )

        tk.Button(
            button_frame,
            text="Upload Document",
            width=20,
            font=("Arial", 11, "bold"),
            bg="#008f5a",
            fg="white",
            command=self.upload_document
        ).grid(
            row=0,
            column=0,
            padx=5,
            pady=5
        )

        tk.Button(
            button_frame,
            text="Talk to Ada",
            width=20,
            font=("Arial", 11, "bold"),
            bg="#0055aa",
            fg="white",
            command=self.talk_to_ada
        ).grid(
            row=0,
            column=1,
            padx=5,
            pady=5
        )

        tk.Button(
            button_frame,
            text="Snap Document",
            width=20,
            font=("Arial", 11, "bold"),
            bg="#444444",
            fg="white",
            command=self.snap_document
        ).grid(
            row=1,
            column=0,
            padx=5,
            pady=5
        )

        tk.Button(
            button_frame,
            text="Back",
            width=20,
            font=("Arial", 11, "bold"),
            bg="#990000",
            fg="white",
            command=self.go_back
        ).grid(
            row=1,
            column=1,
            padx=5,
            pady=5
        )

        input_frame = tk.Frame(
            self.parent,
            bg="#0b0b0b"
        )

        input_frame.pack(
            fill="x",
            padx=20,
            pady=15
        )

        self.message_entry = tk.Entry(
            input_frame,
            font=("Arial", 12),
            bg="white",
            fg="black"
        )

        self.message_entry.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 10)
        )

        self.message_entry.bind(
            "<Return>",
            lambda event: self.send_message()
        )

        tk.Button(
            input_frame,
            text="Send",
            width=10,
            font=("Arial", 11, "bold"),
            bg="#008f5a",
            fg="white",
            command=self.send_message
        ).pack(
            side="right"
        )


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Ada Buttons")
    root.geometry("600x300")
    root.configure(bg="#0b0b0b")

    AdaButtons(root)

    root.mainloop() 
