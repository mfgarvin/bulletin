"""Bulletin extraction using GPT-4o with structured output."""

import base64
from typing import Literal

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from schemas import BulletinExtraction
from utils.retry import retry_async

ExtractionMethod = Literal["direct_pdf", "marker_ocr"]

SYSTEM_PROMPT = """You are an expert at extracting structured information from Catholic parish bulletins.

Extract the following information:

1. **Parish contact info**: Parish name, address, city, state, zip code, phone, website, email (if present)

2. **Regular Mass schedule**: Weekly recurring masses only
   - Include the day of week and time (24-hour format, e.g., 1630 for 4:30pm)
   - Note if a Mass is in a specific language (Spanish, Latin, etc.)
   - Add notes for special conditions (e.g., "First Friday only")

3. **Confession schedule**: Regular confession times
   - Include day, start time, and end time

4. **Adoration schedule**: Eucharistic adoration
   - Set is_perpetual to true if adoration is 24/7
   - Otherwise list the specific times

5. **Parish events**: Both one-time and recurring events
   - One-time: retreats, fish fries, special masses, fundraisers, parish picnics
   - Recurring: bible study, RCIA, youth group, Knights of Columbus meetings
   - Include dates for one-time events, day of week for recurring

6. **Events summary**: Write a brief 2-3 sentence summary of what's happening at this parish. Highlight the most notable upcoming events or ongoing programs. Write in a friendly, informative tone. Make these times 12-hour format.

IMPORTANT GUIDELINES:
- Use 24-hour time format (e.g., 900 for 9:00am, 1630 for 4:30pm) except where requested otherwise.
- Only include REGULAR weekly Mass times, not special occasion masses
- For events, distinguish between one_time and recurring frequencies
- If information is unclear or not present, omit it rather than guessing
- Note any extraction difficulties in extraction_notes
- All times are local to the parish"""


class BulletinExtractor:
    """Extracts structured data from parish bulletins using a single LLM call."""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        method: ExtractionMethod = "direct_pdf",
        model: str = "gpt-5.2",
    ):
        self.client = openai_client
        self.method = method
        self.model = model
        self._marker_converter = None  # Lazy init if needed

    async def extract(self, pdf_bytes: bytes) -> BulletinExtraction:
        """Extract all information from bulletin in a single LLM call."""
        if self.method == "direct_pdf":
            return await self._extract_direct(pdf_bytes)
        else:
            return await self._extract_with_marker(pdf_bytes)

    @retry_async(max_attempts=3, retryable_exceptions=(APITimeoutError, APIConnectionError))
    async def _extract_direct(self, pdf_bytes: bytes) -> BulletinExtraction:
        """Send PDF directly to GPT-4o (native PDF support)."""
        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

        response = await self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "file",
                            "file": {
                                "filename": "bulletin.pdf",
                                "file_data": f"data:application/pdf;base64,{pdf_base64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all parish information from this bulletin.",
                        },
                    ],
                },
            ],
            response_format=BulletinExtraction,
            service_tier="flex",
        )

        return response.choices[0].message.parsed

    @retry_async(max_attempts=3, retryable_exceptions=(APITimeoutError, APIConnectionError))
    async def _extract_with_marker(self, pdf_bytes: bytes) -> BulletinExtraction:
        """Convert PDF to markdown with Marker, then send to LLM."""
        markdown_text = await self._pdf_to_markdown(pdf_bytes)

        response = await self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Extract all parish information from this bulletin:\n\n{markdown_text}",
                },
            ],
            response_format=BulletinExtraction,
            service_tier="flex",
        )

        return response.choices[0].message.parsed

    async def _pdf_to_markdown(self, pdf_bytes: bytes) -> str:
        """Convert PDF to markdown using Marker."""
        import tempfile

        # Lazy import marker (it has heavy dependencies)
        if self._marker_converter is None:
            from marker.converters.pdf import PdfConverter
            from marker.models import create_model_dict

            self._marker_converter = PdfConverter(artifact_dict=create_model_dict())

        # Write PDF to temp file (marker requires a file path)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as f:
            f.write(pdf_bytes)
            f.flush()
            result = self._marker_converter(f.name)
            return result.markdown
