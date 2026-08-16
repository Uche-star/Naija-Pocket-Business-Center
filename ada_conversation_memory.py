"""
ada_conversation_memory.py

Conversation Memory
Naija Pocket Business Center

Stores the current conversation between
Ada and the customer.
"""


class AdaConversationMemory:

    # ==========================================
    # INITIALIZE
    # ==========================================

    def __init__(self):
        self.messages = []

    # ==========================================
    # CLEAR MEMORY
    # ==========================================

    def clear(self):
        self.messages.clear()

    # ==========================================
    # ADD CUSTOMER MESSAGE
    # ==========================================

    def add_customer_message(self, message):

        if message is None:
            return

        message = str(message).strip()

        if not message:
            return

        self.messages.append(
            f"Customer:\n{message}"
        )

    # ==========================================
    # ADD ADA MESSAGE
    # ==========================================

    def add_ada_message(self, message):

        if message is None:
            return

        message = str(message).strip()

        if not message:
            return

        self.messages.append(
            f"Ada:\n{message}"
        )

    # ==========================================
    # GET CONVERSATION
    # ==========================================

    def get_conversation(self):

        return "\n\n".join(
            self.messages
        )

    # ==========================================
    # HAS CONVERSATION
    # ==========================================

    def has_conversation(self):

        return bool(
            self.messages
        )

    # ==========================================
    # GET MESSAGE COUNT
    # ==========================================

    def get_message_count(self):

        return len(
            self.messages
        ) 
