"""A refused request, with the HTTP status the API answers with and a sentence for the person who asked."""


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message
