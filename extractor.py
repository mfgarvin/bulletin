"""Bulletin extraction using GPT-4o with structured output."""

import base64
from typing import Literal

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from schemas import BulletinExtraction
from utils.pdf_compress import compress_if_needed
from utils.retry import retry_async

ExtractionMethod = Literal["direct_pdf", "marker_ocr"]

SYSTEM_PROMPT = """You are an expert at extracting structured information from Catholic parish bulletins.

Extract the following information:

1. **Parish contact info**: Parish name, phone, website, email (shared across all sites)

2. **Sites/Locations**: Extract EACH worship site separately with its own:
   - site_name: Name of the specific location (e.g., "St. Mary Main Church", "Holy Family Chapel")
   - address, city, state, zipcode: Physical location for mapping
   - mass_times: Mass schedule for THIS site — both recurring weekly Masses and one-off dated Masses
   - confession_times: Confession schedule for THIS site
   - adoration: Adoration schedule for THIS site

   IMPORTANT: If the bulletin covers multiple parishes or mission churches with their own
   addresses, create a SEPARATE site entry for each. Side chapels on the SAME campus as the
   main church are NOT separate sites — fold their schedules into the main site. Most
   bulletins have exactly one site.

3. **Mass schedule** (per site):
   - Day of week + time (24hr, e.g. 1630 for 4:30pm)
   - **`mass_date`** — set on every non-recurring Mass; leave null only for truly recurring weekly Masses.
     - Recurring (null): "Sunday at 9am every week", "Daily Mass M-F at 7am", standing Saturday Vigil.
     - Dated (YYYY-MM-DD): holidays (Christmas, Easter), Holy Days of Obligation, civic-day Masses
       (Memorial Day, Labor Day, Thanksgiving), one-off parish Masses (First Communion, graduations,
       jubilees), and ANY Mass listed under a date-specific heading or "this week's schedule" block.
   - `language`: set whenever the bulletin identifies one (Spanish, Latin, Polish, Slovenian,
     Vietnamese, "bilingual," "English & Spanish"…). Put it in this structured field — not only
     in `notes`.
   - `notes`: brief description ("Vigil Mass", "Christmas Eve", "Holy Day of Obligation")
   - A Vigil Mass is always an evening Mass. If you are about to record a vigil before noon,
     you have flipped AM/PM — a "Saturday Vigil at 5:00" means 1700, not 500.

   **DO NOT include private Masses** — weddings, funerals, baptism Masses, ordination Masses
   tied to specific families. Even if dated, these are not part of the public parish schedule.

   **"This week's liturgy" / "Mass intentions" listings.** Bulletins commonly print the upcoming
   week's Masses day-by-day with intentions ("Mon 7am — †John Smith"). These are NOT extra
   Masses on top of the recurring schedule — they ARE the recurring schedule with intentions
   printed alongside. Extract them ONCE as the recurring weekly Mass (`mass_date: null`).
   The only exception: a Mass in that listing whose day/time differs from the standing schedule
   (e.g. Thanksgiving Day has a 9:30am Mass not normally offered) — those ARE dated one-offs
   and need `mass_date`.

   WORKED EXAMPLE. Suppose the bulletin contains:

     This Week's Liturgy
     ───────────────────
     Sun 1/11   8:00 AM  — †Mary Smith
     Sun 1/11  10:00 AM  — People of the Parish
     Sun 1/11  12:00 PM  — †John Doe
     Mon 1/12   7:00 PM  — †Anna Brown
     Tue 1/13   7:00 AM  — †Robert White
     ... (and so on for every day of the week)

   The CORRECT extraction is the recurring weekly schedule, with NO mass_date set on any
   of these entries:
     [{day:"Sunday", time:800, mass_date:null}, {day:"Sunday", time:1000, mass_date:null},
      {day:"Sunday", time:1200, mass_date:null}, {day:"Monday", time:1900, mass_date:null},
      {day:"Tuesday", time:700, mass_date:null}, ...]

   The INCORRECT extraction (do NOT do this) would be seven dated one-offs:
     [{day:"Sunday", time:800, mass_date:"2026-01-11"}, {day:"Monday", time:1900,
      mass_date:"2026-01-12"}, ...] — these are the standard weekly Masses, not one-offs.

   Only add `mass_date` if a row in that listing shows a time or day that ISN'T part of
   the standing schedule (e.g. "Thu 11/27 9:30 AM — Thanksgiving Day Mass" when the parish
   doesn't normally have a 9:30am Thursday Mass).

   **Cancellations.** If a recurring Mass is cancelled on a specific date ("No 7pm Mass on
   Sept 5 due to Labor Day"), do not add a separate cancellation entry — just note it in
   extraction_notes. The recurring Mass entry remains. NEVER encode a cancellation as a Mass
   entry (e.g. a Mass at time 0 with the note "No Mass") — that reads downstream as a real
   midnight Mass.

   **Do not emit duplicates.** One entry per (day, time, language, mass_date). If the bulletin
   prints the same Mass in two places (a schedule box and a "this week" listing), extract it once.

4. **Confession schedule** (per site): day, start_time, end_time.
   - If two listings share a day and start time and one is only an addendum ("or by appointment,"
     "or call the parish office"), emit ONE slot and put the addendum in `notes`.
   - **A list of times is not a range.** "7:45 am & 11:30 am" is TWO slots, not one slot running
     7:45-11:30. Same for "7:45, 11:30" and "7:45 and 11:30". Only an explicit range marker - a
     dash, "to", "until", "-" - joins two times into one slot. This applies inside a day range
     too: "Monday-Friday: 7:45 am & 11:30 am" is two slots on each of the five days, ten in all.
     Parishes commonly schedule confessions in short blocks bracketing a weekday Mass, so a
     multi-hour confession window is the rare case, not the default.
   - If a start is given with no end ("Confessions at 5:00 pm", "after the 8:00 am Mass"), keep
     the slot and OMIT `end_time` entirely. Never invent an end time, never stretch the slot to
     the next listed start, and never repeat the start as the end - a slot from 16:00 to 16:00
     means something different downstream.
   - **A stated duration IS a stated end.** "Confessions 30 minutes before each Mass" bounds the
     slot at both ends: before an 8:00 am Mass it runs 7:30-8:00. Likewise "one hour before Mass"
     or "for a half hour following the vigil". Here the bulletin states the length of the window,
     so emit `end_time`. The rule above is about a window whose length the bulletin never states
     ("confessions after the 8:15 Mass") - that one keeps its `end_time` omitted.
   - **"By appointment" is never its own slot.** A clause like "and by appointment", "or call
     the parish office", "available upon request" carries no day and no time. Attach it to the
     `notes` of the confession slots that are listed, and do not invent a day or time for it.
     "Saturday, 2:30-3:45 PM, the Thursday before First Friday 7:00-8:00 PM and by appointment"
     is TWO slots, both noting that appointments are also available - not three.
   - **Do not anchor a confession to a Mass the bulletin didn't tie it to.** A Mass time listed
     elsewhere on the page (a Mass schedule sidebar, a vigil time) is not evidence of confession
     at or after that Mass. Only emit a slot when the bulletin says confession happens then.
     Never pull a time in from a different sacrament's paragraph (baptism, anointing, marriage).
   - **A day-specific line ADDS to a day-range line; it does not replace it.** Given
     "Monday-Friday: 7:45 am & 11:30 am" followed by "Wednesday: 5:00-5:25 pm", Wednesday has
     THREE slots (7:45, 11:30, and 5:00-5:25) - the second line is an extra Wednesday offering,
     not a correction of the first. Only drop the general line's times for that day when the
     bulletin says so explicitly ("no morning confessions on Wednesday", "Wednesday: 5:00 pm
     only", "except Wednesday"). The same applies to Mass and adoration listings.
   - When the bulletin prints an explicit range, use its stated end verbatim. Do not round it to
     the next half hour or extend it to a following event: "5:00-5:25 pm" followed by "Vespers
     at 5:30" is a slot ending at 5:25, not 5:30.

5. **Adoration schedule** (per site):
   - Set `is_perpetual: true` ONLY if the bulletin explicitly uses the words "perpetual adoration"
     or describes 24-hour / 24/7 / round-the-clock adoration. Do NOT infer it from First Friday
     adoration, weekly Holy Hour, or post-Mass adoration. If the bulletin says the chapel closes
     overnight, or gives the hours it is open, it is NOT perpetual.
   - **When adoration is perpetual, `is_perpetual: true` IS the whole schedule — leave `times`
     empty.** A chapel open 24/7 has no hours to enumerate.
   - **Hours listed as needing adorers are NOT the schedule.** "Hours needing coverage", "open
     hours", "adorers needed: Mon 4 AM, Fri 10 AM", a sign-up sheet or commitment list — these
     name the hours the chapel is *thinly attended* and is appealing for volunteers. Emitting
     them as adoration times says adoration happens ONLY then, which is the opposite of the
     truth, and it is most misleading at exactly the perpetual chapels that run such appeals.
     Never turn them into adoration slots. A perpetual chapel that lists ten hours needing
     coverage still has `is_perpetual: true` and an empty `times`.
   - Otherwise list specific time slots.
   - Same end-time rule as confessions: if adoration has a stated start and no stated end
     ("adoration begins after the 9:00 Mass"), keep the slot and omit `end_time`.
   - A day covered from midnight to midnight (a middle day of a multi-day adoration) is
     `start_time: 0, end_time: 0, end_next_day: true` - a full 24 hours, not an unknown end.

**TIME ENCODING RULES (all schedules):**
- Midnight is `0`, never `2400` and never `240`. A slot running "8:30 PM until Midnight" is
  `start_time: 2030, end_time: 0, end_next_day: true`.
- Set `end_next_day: true` on any slot that crosses midnight (overnight adoration, all-night
  vigils). An overnight slot ending at 6 AM is `start_time: 2200, end_time: 600,
  end_next_day: true`.
- If a slot has NO usable time at all, OMIT the entry entirely. Never use `0` or `00:00` as a
  placeholder for "time unknown" — downstream that becomes a real midnight event. This is about
  a missing *start*: a slot with a known start and an unknown end is still worth having, so keep
  it and omit only `end_time`.
- **How long a Mass runs**, when the bulletin doesn't say: about 1 hour for a Sunday Mass or a
  Saturday vigil, about 30 minutes for a weekday Mass. Use this for ONE purpose — to place the
  start of something the bulletin schedules relative to a Mass. "Confessions after the 8:00 AM
  Sunday Mass" starts at about 9:00; "confessions after the 8:15 AM weekday Mass" at about 8:45.
  Never use the Mass's own start as the start of what follows it, and put the bulletin's wording
  ("After the 8:00 AM Mass") in `notes` so the anchor is visible.
  This is NOT a licence to compute an end time. An event that follows a Mass still has no stated
  end, so `end_time` is still omitted. Never write a note saying an end was estimated, assumed,
  or inferred — if you find yourself wanting to, omit `end_time` instead.
  The one exception is a duration the bulletin itself states ("30 minutes before each Mass"),
  which bounds the window and gives you a real `end_time` — see the confession rules above.

6. **Parish events** (shared across all sites): retreats, fish fries, bible studies, RCIA,
   youth group, Knights of Columbus, fundraisers, etc.
   - Recurring monthly Masses (e.g., monthly Anointing of the Sick Mass on first Monday) belong
     here as events with `frequency: first_friday` or `other_recurring`, NOT in mass_times.
   - Use `one_time` for things on a specific date; `weekly`/`biweekly`/`monthly`/`first_friday`/
     `other_recurring` otherwise.

7. **Events summary**: brief 2-3 sentence friendly summary of notable upcoming events or
   ongoing programs. Use 12-hour format here.

IMPORTANT GUIDELINES:
- 24-hour time format everywhere except events_summary (900 = 9:00am, 1630 = 4:30pm).
- For `mass_date`, infer the year from the bulletin date — if the bulletin is dated December 2025
  and mentions "Christmas Eve Mass," that's 2025-12-24, not 2024 or 2026.
- All times are local to the parish.
- If information is unclear, ambiguous, or not present, omit it. Do not guess.
- Note any extraction difficulties or ambiguity in `extraction_notes`.
- Don't refer to bulletin pages, padding text, or unrelated decoration in any field. Just the schedule."""


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

    async def extract(
        self, content: bytes, content_type: str = "pdf"
    ) -> BulletinExtraction:
        """Extract all information from bulletin in a single LLM call.

        Args:
            content: The bulletin content (PDF bytes or text bytes).
            content_type: Type of content - "pdf", "html", or "text".
        """
        if content_type in ("html", "text"):
            return await self._extract_from_text(content)
        elif self.method == "direct_pdf":
            return await self._extract_direct(content)
        else:
            return await self._extract_with_marker(content)

    @retry_async(max_attempts=3, retryable_exceptions=(APITimeoutError, APIConnectionError))
    async def _call_llm(self, user_content) -> BulletinExtraction:
        """Single point for the LLM call shared by all extraction paths."""
        response = await self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format=BulletinExtraction,
            service_tier="flex",
        )
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("LLM returned no parsed content (refusal or parse failure)")
        return parsed

    async def _extract_direct(self, pdf_bytes: bytes) -> BulletinExtraction:
        """Send PDF directly to the model (native PDF support)."""
        pdf_bytes = await compress_if_needed(pdf_bytes)
        pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        return await self._call_llm([
            {
                "type": "file",
                "file": {
                    "filename": "bulletin.pdf",
                    "file_data": f"data:application/pdf;base64,{pdf_base64}",
                },
            },
            {"type": "text", "text": "Extract all parish information from this bulletin."},
        ])

    async def _extract_with_marker(self, pdf_bytes: bytes) -> BulletinExtraction:
        """Convert PDF to markdown with Marker, then send to LLM."""
        markdown_text = await self._pdf_to_markdown(pdf_bytes)
        return await self._call_llm(
            f"Extract all parish information from this bulletin:\n\n{markdown_text}"
        )

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

    async def _extract_from_text(self, text_bytes: bytes) -> BulletinExtraction:
        """Extract from text/markdown content (for webpage bulletins)."""
        text_content = text_bytes.decode("utf-8")
        return await self._call_llm(
            f"Extract all parish information from this bulletin webpage:\n\n{text_content}"
        )
