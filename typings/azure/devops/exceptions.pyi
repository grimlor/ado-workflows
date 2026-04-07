"""Partial stubs for azure.devops.exceptions — only types used by ado-workflows."""

class AzureDevOpsAuthenticationError(Exception):
    message: str
    inner_exception: Exception | None
    def __init__(
        self,
        message: str,
        inner_exception: Exception | None = None,
        *args: object,
        **kwargs: object,
    ) -> None: ...

class AzureDevOpsServiceError(Exception):
    message: str
    inner_exception: AzureDevOpsServiceError | None
    exception_id: str
    type_name: str
    type_key: str
    error_code: int
    event_id: int
    custom_properties: dict[str, object]
    def __init__(self, wrapped_exception: object) -> None: ...
