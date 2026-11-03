"""Non-text tool result content, mirroring MCP content blocks.

An MCP server returns images as image content blocks; through Archipelago's
gateway the agent loop delivers them to the model. Atoll tools return an
``ImageContent`` for the same effect: the provider passes it through untouched
and the loop attaches it to the conversation as an image.
"""

import base64

from pydantic import BaseModel


class ImageContent(BaseModel):
    """One image result (the MCP ``image`` content block equivalent)."""

    mime_type: str
    base64_data: str

    @classmethod
    def from_bytes(cls, data: bytes, mime_type: str) -> "ImageContent":
        return cls(mime_type=mime_type, base64_data=base64.b64encode(data).decode("ascii"))

    @property
    def data_uri(self) -> str:
        return f"data:{self.mime_type};base64,{self.base64_data}"
