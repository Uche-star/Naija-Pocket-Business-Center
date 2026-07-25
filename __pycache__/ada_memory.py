"""
ada_memory.py
Conversation Memory for Ada
Naija Pocket Business Center
"""

class AdaMemory:

    def __init__(self):
        self.clear()

    def clear(self):
        self.data = {
            "service": None,
            "status": "waiting",
            "document_received": False,
            "customer_information": {}
        }

    def set_service(self, service):
        self.data["service"] = service

    def get_service(self):
        return self.data["service"]

    def set_status(self, status):
        self.data["status"] = status

    def get_status(self):
        return self.data["status"]

    def document_received(self):
        self.data["document_received"] = True

    def has_document(self):
        return self.data["document_received"]

    def save_information(self, key, value):
        self.data["customer_information"][key] = value

    def get_information(self, key):
        return self.data["customer_information"].get(key)

    def get_memory(self):
        return self.data


if __name__ == "__main__":

    memory = AdaMemory()

    memory.set_service("assignment_typing")
    memory.set_status("waiting_for_document")

    print(memory.get_memory())

    memory.document_received()

    print(memory.get_memory()) 
