from typing import Any

class Connection:
    def __init__(
        self,
        base_url: str | None = None,
        creds: Any | None = None,
        user_agent: str | None = None,
    ) -> None: ...
    def get_client(self, client_type: str) -> Any: ...
