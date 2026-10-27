"""Shared Claude (Anthropic) extraction utility for document processing."""
import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def call_claude_async(
    file_path: str,
    prompt: str,
    api_key: str,
    model: str = "claude-opus-4-7",
    system_prompt: str = "You are an expert document data extractor. Return only valid JSON.",
    max_tokens: int = 8192,
) -> str:
    """
    Call Claude API with a file (PDF or image) and a text prompt.
    Returns the raw text response from Claude.

    Supports:
    - PDF: sent as base64 document (Claude reads natively)
    - Images (.png, .jpg, .jpeg, .gif, .webp): sent as base64 image
    - Other: text-only prompt (uses extracted_text passed in prompt)
    """
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)

    content = []

    if file_path:
        file_ext = os.path.splitext(file_path)[1].lower()
        image_exts = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                      '.gif': 'image/gif', '.webp': 'image/webp'}
        try:
            if file_ext == '.pdf':
                with open(file_path, 'rb') as f:
                    pdf_b64 = base64.standard_b64encode(f.read()).decode('utf-8')
                content.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    }
                })
                logger.info("Attached PDF document to Claude request")
            elif file_ext in image_exts:
                with open(file_path, 'rb') as f:
                    img_b64 = base64.standard_b64encode(f.read()).decode('utf-8')
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image_exts[file_ext],
                        "data": img_b64,
                    }
                })
                logger.info(f"Attached image ({file_ext}) to Claude request")
        except Exception as e:
            logger.warning(f"Could not attach file to Claude request: {e}")

    content.append({"type": "text", "text": prompt})

    response = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
    )

    return response.content[0].text
