"""
Naija Pocket Business Center
INTELLIGENCE-FIRST DOCUMENT ENGINE

COMPLETE REPLACEMENT: ada_response.py

Purpose:
- Ada is the intelligence layer.
- Documents are professionally standardized before Workspace/Review.
- Review examines the already-standardized document.
- Review does NOT regenerate the complete document.
- Customer corrections are applied first, then standardized once.
- No review_prompt.py import.
- Groq token-limit constants remain unchanged.
- No blind Markdown stripping or mechanical document rewriting.
"""

from __future__ import annotations

import hashlib
import os
import re
import traceback
from typing import Any, Callable

try:
    from groq import Groq
except ImportError:
    Groq = None

from billing_manager import BillingManager


# ============================================================
# EXISTING TOKEN / REQUEST LIMITS — DO NOT CHANGE
# ============================================================

MAX_SYSTEM_PROMPT_CHARS = 5000
MAX_HISTORY_MESSAGES = 4
MAX_HISTORY_MESSAGE_CHARS = 900
MAX_USER_MESSAGE_CHARS = 4500
MAX_CONTEXT_CHARS = 1800

MAX_DOCUMENT_PAGES = 1000

GENERATION_REQUEST_CHARS = 8500
GENERATION_OUTPUT_TOKENS = 5000
MAX_GENERATION_PARTS = 20

REVIEW_REQUEST_CHARS = 12000
REVIEW_OUTPUT_TOKENS = 700

CORRECTION_REQUEST_CHARS = 10500
CORRECTION_OUTPUT_TOKENS = 4500

DEFAULT_PAGE_CHARS = 7000

END_OF_DOCUMENT_MARKER = "[END OF DOCUMENT]"
CONTINUE_MARKER = "[CONTINUE]"
CONTINUATION_TAIL_CHARS = 3000


# ============================================================
# ERRORS
# ============================================================

class AdaResponseError(RuntimeError):
    """Controlled Ada response error."""


# ============================================================
# ADA RESPONSE
# ============================================================

class AdaResponse:
    """
    Customer-facing Ada document intelligence.

    Important architecture:

        Customer request
              ↓
        Ada intelligence
              ↓
        Document generation
              ↓
        ONE standardization pass
              ↓
        Workspace / Review

    Review:
        standardized document
              ↓
        findings-only review
              ↓
        Review page

    Correction:
        standardized document
              ↓
        correction instruction
              ↓
        corrected document
              ↓
        ONE standardization pass
              ↓
        Review
    """

    def __init__(self, service: str | None = None):
        self.service = service
        self.billing = BillingManager()

        self.history: list[dict[str, str]] = []

        self.active_document_text: str = ""
        self.active_document_path: str | None = None

        self._client = None
        self._model = None

        self._review_cache: dict[str, dict[str, Any]] = {}

        self._load_groq()

    # ========================================================
    # GROQ
    # ========================================================

    def _load_groq(self) -> None:
        if Groq is None:
            self._client = None
            self._model = None
            return

        api_key = ""
        model = ""

        try:
            from ada_ai_config import API_KEY, MODEL

            api_key = str(API_KEY or "").strip()
            model = str(MODEL or "").strip()
        except Exception:
            api_key = os.getenv("GROQ_API_KEY", "").strip()
            model = os.getenv("GROQ_MODEL", "").strip()

        if not api_key:
            api_key = os.getenv("GROQ_API_KEY", "").strip()

        if not model:
            model = os.getenv("GROQ_MODEL", "").strip()

        if not api_key:
            self._client = None
            self._model = model or None
            return

        try:
            self._client = Groq(api_key=api_key)
            self._model = model or "llama-3.3-70b-versatile"
        except Exception:
            self._client = None
            self._model = model or None

    # ========================================================
    # SERVICE
    # ========================================================

    def set_service(self, service: str | None) -> None:
        if service:
            self.service = self.normalize_service(service)

    @staticmethod
    def normalize_service(service: Any) -> str:
        if service is None:
            return ""

        text = str(service).strip()

        if not text:
            return ""

        text = re.sub(r"\s+", " ", text)
        return text

    # ========================================================
    # BILLING CONTEXT
    # ========================================================

    def get_billing_context(self, service: str | None = None) -> str:
        resolved = self.normalize_service(service or self.service)

        if not resolved:
            return ""

        try:
            data = self.billing.get_service(resolved)

            if not isinstance(data, dict):
                return ""

            price = data.get("price")
            billing = data.get("billing")

            parts: list[str] = []

            if price is not None:
                parts.append(f"Price: ₦{price}")

            if billing:
                parts.append(f"Billing: {billing}")

            return " | ".join(parts)

        except Exception:
            return ""

    # ========================================================
    # ADA INTELLIGENCE RULES
    # ========================================================

    def intelligence_rules(self) -> str:
        return """
You are Ada, Naija Pocket Business Center's agent.

You are the customer-facing intelligence responsible for understanding
the customer's request and doing the requested work.

IMPORTANT:

1. The selected service is context, not a rigid template.
2. Understand the customer's actual instruction.
3. Preserve facts supplied by the customer.
4. Do not invent facts, names, dates, prices, qualifications,
   addresses, experience, statistics, or other information.
5. Ask only for information that is genuinely necessary.
6. Ask one question at a time.
7. Use natural Nigerian English where appropriate.
8. You may naturally use simple Nigerian expressions or Pidgin
   when suitable for the conversation.
9. Never mention Groq, models, tokens, prompts, internal systems,
   backend errors, or technical implementation to the customer.
10. Do not describe yourself as a generic AI.
11. Work directly on the customer's request.

DOCUMENT RULES:

When producing a document, the final customer document must be
professionally structured and readable.

The final document must NOT expose Markdown source syntax.

Do not output:
- Markdown headings such as #, ##, ###
- Markdown bold such as **text**
- Markdown italic syntax
- Markdown bullet syntax using *
- Markdown table pipes
- Markdown separator lines such as ---
- Markdown code fences
- raw Markdown formatting instructions

Instead, produce a clean professional document using:
- clear headings
- proper paragraph spacing
- properly separated sections
- numbered lists where appropriate
- readable tables where a table is genuinely required
- consistent capitalization
- professional punctuation
- professional Nigerian business/document conventions

Do not blindly remove formatting characters from meaningful content.
Use document intelligence to determine the intended structure.

All customer-provided writing must also be professionally standardized.
This includes:
- typed material
- OCR material
- corrections
- rewrites
- proofreading
- edited material
- generated documents
- revised documents
"""

    # ========================================================
    # STATIC SYSTEM PROMPT
    # ========================================================

    def _build_static_system_base(self) -> str:
        return self.intelligence_rules()

    def build_system_prompt(
        self,
        service: str | None = None,
        context: str | None = None,
    ) -> str:

        service_text = self.normalize_service(service or self.service)
        billing_context = self.get_billing_context(service_text)

        prompt = self._build_static_system_base()

        if service_text:
            prompt += f"""

CURRENT SERVICE CONTEXT:
{service_text}
"""

        if billing_context:
            prompt += f"""

CURRENT SERVICE BILLING INFORMATION:
{billing_context}
"""

        if context:
            prompt += f"""

RELEVANT CUSTOMER CONTEXT:
{str(context)[:MAX_CONTEXT_CHARS]}
"""

        return prompt[:MAX_SYSTEM_PROMPT_CHARS]

    # ========================================================
    # HISTORY
    # ========================================================

    def add_history(self, role: str, content: str) -> None:
        text = str(content or "").strip()

        if not text:
            return

        self.history.append(
            {
                "role": role,
                "content": text[:MAX_HISTORY_MESSAGE_CHARS],
            }
        )

        if len(self.history) > MAX_HISTORY_MESSAGES:
            self.history = self.history[-MAX_HISTORY_MESSAGES:]

    def clear_history(self) -> None:
        self.history.clear()

    # ========================================================
    # GROQ CALL
    # ========================================================

    def call_groq(
        self,
        system_prompt: str,
        user_prompt: str,
        output_tokens: int,
    ) -> str:

        if self._client is None:
            raise AdaResponseError(
                "Groq intelligence is not configured."
            )

        if not self._model:
            raise AdaResponseError(
                "Groq model is not configured."
            )

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": system_prompt[:MAX_SYSTEM_PROMPT_CHARS],
            }
        ]

        for item in self.history[-MAX_HISTORY_MESSAGES:]:
            role = item.get("role", "user")
            content = item.get("content", "")

            if role not in {"user", "assistant"}:
                continue

            messages.append(
                {
                    "role": role,
                    "content": str(content)[:MAX_HISTORY_MESSAGE_CHARS],
                }
            )

        messages.append(
            {
                "role": "user",
                "content": user_prompt,
            }
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.2,
                max_completion_tokens=output_tokens,
            )

            choices = getattr(response, "choices", None)

            if not choices:
                raise AdaResponseError(
                    "Groq returned no response."
                )

            message = getattr(choices[0], "message", None)

            if message is None:
                raise AdaResponseError(
                    "Groq returned an invalid response."
                )

            content = getattr(message, "content", None)

            if content is None:
                raise AdaResponseError(
                    "Groq returned empty content."
                )

            result = str(content).strip()

            if not result:
                raise AdaResponseError(
                    "Groq returned empty content."
                )

            return result

        except AdaResponseError:
            raise

        except Exception as error:
            raise AdaResponseError(
                f"Groq request failed: {error}"
            ) from error

    # ========================================================
    # STANDARDIZATION PROMPT
    # ========================================================

    def build_standardization_prompt(
        self,
        document: str,
        service: str | None = None,
        purpose: str = "final customer document",
    ) -> str:

        service_text = self.normalize_service(service or self.service)

        return f"""
STANDARDIZE THE DOCUMENT BELOW.

Purpose:
{purpose}

Service:
{service_text or "Document service"}

Your task is to use document intelligence to transform the supplied
document into a clean, professional, customer-ready document.

CRITICAL REQUIREMENTS:

- Preserve every important fact and meaning.
- Do not invent information.
- Do not remove meaningful customer information.
- Correct grammar and obvious spelling errors where appropriate.
- Improve professional structure.
- Separate headings from body text.
- Separate labels such as "To:", "From:", "Date:" properly.
- Preserve numbered lists.
- Preserve useful bullet/list structure.
- Present tables as readable professional tables.
- Restore sensible paragraph spacing.
- Prevent headings and paragraphs from running together.
- Do not output Markdown source syntax.
- Do not use # or ## headings.
- Do not use **bold** syntax.
- Do not use Markdown table pipes.
- Do not use Markdown separator lines.
- Do not use code fences.
- Do not add commentary before or after the document.
- Return ONLY the complete standardized document.

The output is going directly to the customer-facing Workspace/Review
document area, so it must already be professionally readable.

DOCUMENT TO STANDARDIZE:

{document[:DEFAULT_PAGE_CHARS]}
"""

    # ========================================================
    # INTELLIGENT STANDARDIZATION
    # ========================================================

    def intelligently_standardize_pages(
        self,
        pages: list[dict[str, Any]],
        service: str | None = None,
        purpose: str = "final customer document",
    ) -> list[dict[str, Any]]:

        normalized = self.normalize_document_pages(pages)

        if not normalized:
            return []

        standardized_pages: list[dict[str, Any]] = []

        for index, page in enumerate(normalized, start=1):

            content = str(page.get("content", "")).strip()

            if not content:
                continue

            prompt = self.build_standardization_prompt(
                document=content,
                service=service,
                purpose=purpose,
            )

            result = self.call_groq(
                system_prompt=self.build_system_prompt(service),
                user_prompt=prompt,
                output_tokens=GENERATION_OUTPUT_TOKENS,
            )

            result = result.strip()

            if not result:
                raise AdaResponseError(
                    f"Standardization returned empty content for page {index}."
                )

            standardized_pages.append(
                {
                    "page_number": index,
                    "content": result,
                }
            )

        return standardized_pages

    def intelligently_standardize_document(
        self,
        document: str,
        service: str | None = None,
        purpose: str = "final customer document",
    ) -> str:

        text = str(document or "").strip()

        if not text:
            raise AdaResponseError(
                "There is no document content to standardize."
            )

        pages = self.document_to_pages(text)

        standardized_pages = self.intelligently_standardize_pages(
            pages=pages,
            service=service,
            purpose=purpose,
        )

        if not standardized_pages:
            raise AdaResponseError(
                "Document standardization returned no usable pages."
            )

        standardized = self.assemble_document(
            standardized_pages
        ).strip()

        if not standardized:
            raise AdaResponseError(
                "Document standardization returned empty content."
            )

        self.active_document_text = standardized

        return standardized

    # ========================================================
    # GENERATION PROMPT
    # ========================================================

    def build_generation_prompt(
        self,
        instruction: str,
        service: str | None = None,
        context: str | None = None,
        source_document: str | None = None,
    ) -> str:

        service_text = self.normalize_service(service or self.service)

        prompt = f"""
WORK ON THE CUSTOMER'S REQUEST.

SERVICE:
{service_text or "Not specified"}

CUSTOMER REQUEST:
{instruction[:GENERATION_REQUEST_CHARS]}
"""

        if context:
            prompt += f"""

CONTEXT:
{context[:MAX_CONTEXT_CHARS]}
"""

        if source_document:
            prompt += f"""

SOURCE MATERIAL:

{source_document[:GENERATION_REQUEST_CHARS]}

Use the source material as the authoritative source of facts.
Preserve its meaning while performing the customer's requested work.
"""

        prompt += """

If this is a document task, return the complete finished document.

Do not explain what you are doing.
Do not provide a draft followed by commentary.
Do not mention internal systems.

The final document will go through Ada's professional
standardization stage before reaching the customer.
"""

        return prompt[:GENERATION_REQUEST_CHARS]

    # ========================================================
    # DOCUMENT GENERATION
    # ========================================================

    def generate_document(
        self,
        instruction: str,
        service: str | None = None,
        context: str | None = None,
        source_document: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:

        instruction = str(instruction or "").strip()

        if not instruction:
            raise AdaResponseError(
                "No document instruction was supplied."
            )

        resolved_service = self.normalize_service(
            service or self.service
        )

        prompt = self.build_generation_prompt(
            instruction=instruction,
            service=resolved_service,
            context=context,
            source_document=source_document,
        )

        generated_parts: list[str] = []

        current_prompt = prompt

        for part_number in range(1, MAX_GENERATION_PARTS + 1):

            result = self.call_groq(
                system_prompt=self.build_system_prompt(
                    resolved_service,
                    context,
                ),
                user_prompt=current_prompt,
                output_tokens=GENERATION_OUTPUT_TOKENS,
            ).strip()

            if not result:
                break

            result = result.replace(
                END_OF_DOCUMENT_MARKER,
                ""
            ).strip()

            result = result.replace(
                CONTINUE_MARKER,
                ""
            ).strip()

            generated_parts.append(result)

            if progress_callback:
                try:
                    progress_callback(
                        {
                            "type": "generation_progress",
                            "part": part_number,
                            "total": MAX_GENERATION_PARTS,
                        }
                    )
                except Exception:
                    pass

            lower = result.lower()

            if (
                END_OF_DOCUMENT_MARKER.lower() in lower
                or len(result) < GENERATION_OUTPUT_TOKENS * 2
            ):
                break

            tail = result[-CONTINUATION_TAIL_CHARS:]

            current_prompt = f"""
Continue the document from exactly where you stopped.

Do not repeat previous content.

Return only the remaining document content.

When the entire document is finished, end with:
{END_OF_DOCUMENT_MARKER}

DOCUMENT TAIL:

{tail}
"""

        generated_document = "\n\n".join(
            part for part in generated_parts if part.strip()
        ).strip()

        if not generated_document:
            raise AdaResponseError(
                "Ada generated no usable document."
            )

        # IMPORTANT:
        # Generation performs ONE standardization pass.
        standardized_document = (
            self.intelligently_standardize_document(
                generated_document,
                service=resolved_service,
                purpose="final customer document before Workspace and Review",
            )
        )

        self.active_document_text = standardized_document

        pages = self.document_to_pages(
            standardized_document
        )

        return {
            "success": True,
            "document": standardized_document,
            "document_text": standardized_document,
            "pages": pages,
            "document_pages": pages,
            "service": resolved_service,
        }

    # ========================================================
    # DOCUMENT PAGE HELPERS
    # ========================================================

    @staticmethod
    def document_to_pages(
        document: str,
        page_chars: int = DEFAULT_PAGE_CHARS,
    ) -> list[dict[str, Any]]:

        text = str(document or "").strip()

        if not text:
            return []

        if page_chars <= 0:
            page_chars = DEFAULT_PAGE_CHARS

        paragraphs = re.split(
            r"\n\s*\n",
            text,
        )

        pages: list[dict[str, Any]] = []
        current: list[str] = []
        current_length = 0

        for paragraph in paragraphs:

            paragraph = paragraph.strip()

            if not paragraph:
                continue

            paragraph_length = len(paragraph)

            if (
                current
                and current_length + paragraph_length + 2 > page_chars
            ):
                pages.append(
                    {
                        "page_number": len(pages) + 1,
                        "content": "\n\n".join(current).strip(),
                    }
                )

                current = []
                current_length = 0

            current.append(paragraph)
            current_length += paragraph_length + 2

        if current:
            pages.append(
                {
                    "page_number": len(pages) + 1,
                    "content": "\n\n".join(current).strip(),
                }
            )

        return pages[:MAX_DOCUMENT_PAGES]

    @staticmethod
    def normalize_document_pages(
        pages: Any,
    ) -> list[dict[str, Any]]:

        if not isinstance(pages, list):
            return []

        normalized: list[dict[str, Any]] = []

        for index, page in enumerate(pages, start=1):

            if isinstance(page, str):
                content = page.strip()

            elif isinstance(page, dict):
                content = str(
                    page.get("content")
                    or page.get("text")
                    or page.get("page_content")
                    or ""
                ).strip()

            else:
                continue

            if not content:
                continue

            normalized.append(
                {
                    "page_number": index,
                    "content": content,
                }
            )

        return normalized[:MAX_DOCUMENT_PAGES]

    @staticmethod
    def assemble_document(
        pages: list[dict[str, Any]],
    ) -> str:

        normalized = AdaResponse.normalize_document_pages(
            pages
        )

        return "\n\n".join(
            page["content"]
            for page in normalized
            if page.get("content")
        ).strip()

    # ========================================================
    # REVIEW CACHE
    # ========================================================

    def _review_cache_key(
        self,
        pages: list[dict[str, Any]],
        service: str | None = None,
    ) -> str:

        document = self.assemble_document(pages)

        raw = (
            f"{self.normalize_service(service or self.service)}"
            f"\n{document}"
        )

        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    # ========================================================
    # REVIEW
    # ========================================================

    def review_document_pages(
        self,
        pages: list[dict[str, Any]],
        service: str | None = None,
        context: str | None = None,
        customer_request: str | None = None,
        event: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:

        """
        IMPORTANT:

        The pages arriving here are already the authoritative,
        standardized customer document.

        Review does NOT standardize them again.

        Review does NOT ask Groq to reproduce the entire document.

        Groq returns findings only.
        """

        normalized_pages = self.normalize_document_pages(
            pages
        )

        if not normalized_pages:
            raise AdaResponseError(
                "There is no document available for review."
            )

        # The incoming standardized document is authoritative.
        document = self.assemble_document(
            normalized_pages
        )

        if not document:
            raise AdaResponseError(
                "The document available for review is empty."
            )

        cache_key = self._review_cache_key(
            normalized_pages,
            service,
        )

        cached = self._review_cache.get(cache_key)

        if cached:
            return cached

        # ----------------------------------------------------
        # FINDINGS ONLY
        # ----------------------------------------------------

        review_source = document[:REVIEW_REQUEST_CHARS]

        review_prompt = f"""
REVIEW THE COMPLETE STANDARDIZED DOCUMENT BELOW.

Your task is to identify genuine issues that should be corrected
before customer approval.

IMPORTANT:

- Do NOT reproduce the document.
- Do NOT rewrite the document.
- Do NOT return the complete document.
- Do NOT return Markdown.
- Do NOT use #, **, | tables, or --- separators.
- Do NOT invent problems.
- Do NOT complain about placeholders such as [Your Company Name]
  when they are clearly intentional placeholders.
- Check grammar, spelling, clarity, consistency, numbering,
  professional structure, obvious omissions and contradictions.
- Preserve the customer's meaning.

Return ONLY findings.

Use this exact format:

FINDINGS_START
PAGE 1: finding
PAGE 2: finding
FINDINGS_END

If there are no genuine issues, return:

FINDINGS_START
NO ISSUES FOUND
FINDINGS_END

DOCUMENT:

{review_source}
"""

        findings_response = self.call_groq(
            system_prompt=self.build_system_prompt(
                service,
                context,
            ),
            user_prompt=review_prompt[:REVIEW_REQUEST_CHARS],
            output_tokens=REVIEW_OUTPUT_TOKENS,
        ).strip()

        findings = self._extract_findings(
            findings_response
        )

        review_pages: list[dict[str, Any]] = []

        for index, page in enumerate(
            normalized_pages,
            start=1,
        ):

            page_finding = findings.get(
                index,
                "No issues found."
            )

            review_page = {
                "page_number": index,
                "content": page["content"],
                "text": page["content"],
                "page_content": page["content"],
                "review": page_finding,
                "status": "reviewed",
                "error": None,
            }

            review_pages.append(
                review_page
            )

            if progress_callback:
                try:
                    progress_callback(
                        {
                            "type": "page_completed",
                            "page_number": index,
                            "position": index,
                            "total": len(normalized_pages),
                            "review": page_finding,
                            "content": page["content"],
                        }
                    )
                except Exception:
                    pass

        assembled_review = "\n\n".join(
            f"Page {page['page_number']}: "
            f"{page['review']}"
            for page in review_pages
        )

        result = {
            "success": True,
            "pages": review_pages,
            "document_pages": normalized_pages,
            "document_text": document,
            "assembled_review": assembled_review,
            "findings": findings,
            "status": "review_complete",
        }

        self._review_cache[cache_key] = result

        if progress_callback:
            try:
                progress_callback(
                    {
                        "type": "review_completed",
                        "total": len(normalized_pages),
                        "assembled_review": assembled_review,
                    }
                )
            except Exception:
                pass

        return result

    # ========================================================
    # REVIEW FINDINGS PARSER
    # ========================================================

    @staticmethod
    def _extract_findings(
        response: str,
    ) -> dict[int, str]:

        text = str(response or "").strip()

        if not text:
            return {}

        match = re.search(
            r"FINDINGS_START(.*?)(?:FINDINGS_END|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if match:
            body = match.group(1).strip()
        else:
            body = text

        if not body:
            return {}

        if "NO ISSUES FOUND" in body.upper():
            return {}

        findings: dict[int, str] = {}

        pattern = re.compile(
            r"(?:^|\n)\s*PAGE\s+(\d+)\s*:\s*(.*?)(?=\n\s*PAGE\s+\d+\s*:|\Z)",
            flags=re.IGNORECASE | re.DOTALL,
        )

        for match in pattern.finditer(body):

            try:
                page_number = int(
                    match.group(1)
                )
            except (TypeError, ValueError):
                continue

            finding = match.group(2).strip()

            if finding:
                findings[page_number] = finding

        if not findings and body:
            findings[1] = body

        return findings

    # ========================================================
    # REVIEW ASSEMBLY
    # ========================================================

    @staticmethod
    def assemble_review(
        review_pages: list[dict[str, Any]],
    ) -> str:

        normalized = AdaResponse.normalize_document_pages(
            review_pages
        )

        parts: list[str] = []

        for page in normalized:
            review = str(
                page.get("review")
                or "No issues found."
            ).strip()

            parts.append(
                f"Page {page['page_number']}: {review}"
            )

        return "\n\n".join(parts).strip()

    # ========================================================
    # CORRECTION
    # ========================================================

    def correct_document(
        self,
        pages: list[dict[str, Any]] | None = None,
        correction: str = "",
        service: str | None = None,
        context: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:

        """
        CORRECTION FLOW:

        Existing standardized document
                  ↓
        Customer correction
                  ↓
        Groq applies correction
                  ↓
        ONE intelligent standardization pass
                  ↓
        Corrected standardized document
        """

        instruction = str(
            correction
            or kwargs.get("instruction")
            or kwargs.get("message")
            or ""
        ).strip()

        if not instruction:
            raise AdaResponseError(
                "Correction instruction is empty."
            )

        normalized_pages = self.normalize_document_pages(
            pages or kwargs.get("document_pages")
        )

        if not normalized_pages:
            if self.active_document_text:
                normalized_pages = self.document_to_pages(
                    self.active_document_text
                )

        if not normalized_pages:
            raise AdaResponseError(
                "There is no document available for correction."
            )

        # IMPORTANT:
        # DO NOT standardize here.
        #
        # The document arriving from Review is already standardized.
        #
        current_document = self.assemble_document(
            normalized_pages
        )

        if not current_document:
            raise AdaResponseError(
                "The current document is empty."
            )

        service_text = self.normalize_service(
            service or self.service
        )

        correction_prompt = f"""
APPLY THE CUSTOMER'S CORRECTION TO THE DOCUMENT.

SERVICE:
{service_text or "Document service"}

CUSTOMER CORRECTION:
{instruction[:CORRECTION_REQUEST_CHARS]}

CURRENT DOCUMENT:
{current_document[:CORRECTION_REQUEST_CHARS]}

INSTRUCTIONS:

1. Apply the customer's correction accurately.
2. Preserve all other correct information.
3. Do not invent facts.
4. Do not remove unrelated content.
5. Return the complete corrected document.
6. Do not return an explanation.
7. Do not describe the changes.
8. Do not use Markdown source syntax.
9. Do not use # headings.
10. Do not use **bold** syntax.
11. Do not use Markdown table pipes.
12. Do not use --- separators.
13. Return ONLY the complete corrected document.

The corrected document will receive one final professional
standardization pass after this correction.
"""

        corrected_raw = self.call_groq(
            system_prompt=self.build_system_prompt(
                service_text,
                context,
            ),
            user_prompt=correction_prompt[
                :CORRECTION_REQUEST_CHARS
            ],
            output_tokens=CORRECTION_OUTPUT_TOKENS,
        ).strip()

        if not corrected_raw:
            raise AdaResponseError(
                "Ada returned no corrected document."
            )

        # ----------------------------------------------------
        # ONE AND ONLY ONE STANDARDIZATION PASS AFTER
        # CORRECTION
        # ----------------------------------------------------

        corrected_document = (
            self.intelligently_standardize_document(
                corrected_raw,
                service=service_text,
                purpose="corrected final customer document",
            )
        )

        if not corrected_document:
            raise AdaResponseError(
                "The corrected document could not be standardized."
            )

        self.active_document_text = corrected_document

        corrected_pages = self.document_to_pages(
            corrected_document
        )

        if not corrected_pages:
            raise AdaResponseError(
                "The corrected document contains no usable pages."
            )

        if progress_callback:
            try:
                progress_callback(
                    {
                        "type": "correction_completed",
                        "document_text": corrected_document,
                        "pages": corrected_pages,
                        "total": len(corrected_pages),
                    }
                )
            except Exception:
                pass

        # Clear old review cache because the document changed.
        self._review_cache.clear()

        return {
            "success": True,
            "document": corrected_document,
            "document_text": corrected_document,
            "pages": corrected_pages,
            "document_pages": corrected_pages,
            "service": service_text,
            "status": "corrected",
        }

    # ========================================================
    # NORMAL ADA CHAT
    # ========================================================

    def respond(
        self,
        message: str,
        service: str | None = None,
        context: str | None = None,
        event: str | None = None,
        **kwargs: Any,
    ) -> str:

        user_message = str(message or "").strip()

        if not user_message:
            return (
                "Please tell Ada what you would like help with."
            )

        resolved_service = self.normalize_service(
            service or self.service
        )

        system_prompt = self.build_system_prompt(
            resolved_service,
            context,
        )

        result = self.call_groq(
            system_prompt=system_prompt,
            user_prompt=user_message[
                :MAX_USER_MESSAGE_CHARS
            ],
            output_tokens=CORRECTION_OUTPUT_TOKENS,
        )

        self.add_history(
            "user",
            user_message,
        )

        self.add_history(
            "assistant",
            result,
        )

        return result

    # ========================================================
    # EVENT HELPERS
    # ========================================================

    @staticmethod
    def is_review_event(event: Any) -> bool:
        text = str(event or "").strip().lower()

        return text in {
            "review",
            "send_for_review",
            "review_document",
            "start_review",
        }

    @staticmethod
    def is_correction_event(event: Any) -> bool:
        text = str(event or "").strip().lower()

        return text in {
            "correction",
            "apply_correction",
            "correct_document",
            "review_correction",
        }


# ============================================================
# SIMPLE DIAGNOSTIC
# ============================================================

if __name__ == "__main__":

    print("AdaResponse diagnostic")
    print("----------------------")

    try:
        ada = AdaResponse()

        print(
            "Groq client:",
            "connected" if ada._client else "not connected",
        )

        print(
            "Groq model:",
            ada._model or "not configured",
        )

        print(
            "Service:",
            ada.service or "not set",
        )

    except Exception as error:
        print(
            "AdaResponse error:",
            type(error).__name__,
            str(error),
        )
        traceback.print_exc()
