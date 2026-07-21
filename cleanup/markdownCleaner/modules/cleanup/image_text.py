import re


class ImageTextProcessor:
    """Remove legacy picture-text marker blocks from editable segments.

    This small processor supports older segment-oriented cleanup workflows.
    ``DocumentCleanupStage`` performs the newer whole-document, readability-aware
    picture OCR handling; this processor simply removes every explicitly marked
    block from the segment it receives.

    Example::

        from unittest.mock import Mock

        context = Mock()
        processor = ImageTextProcessor(context)
        segment = Mock(
            current_text=(
                "Story<!-- Start of picture text -->noisy OCR"
                "<!-- End of picture text -->continues"
            )
        )
        assert processor.process(segment) is True
        segment.update.assert_called_once_with("Storycontinues")
    """

    name = "ImageText"

    def __init__(self, context):
        """Bind the processor to the shared processing context.

        Args:
            context: Active context whose ``increment`` method receives the
                ``image_text_removed`` metric when a segment changes.

        Example::

            context = object()
            processor = ImageTextProcessor(context)
            assert processor.context is context

        Construction does not inspect or modify document content.
        """

        self.context = context

    def process(self, segment):
        """Delete all marked picture OCR blocks from one editable segment.

        Args:
            segment: Editable segment exposing ``current_text`` and ``update``.

        Returns:
            ``True`` when at least one complete marker block was removed;
            otherwise ``False``. A successful removal also increments the
            context's ``image_text_removed`` statistic once for that segment.

        Example::

            from unittest.mock import Mock

            context = Mock()
            segment = Mock(
                current_text=(
                    "Before\n<!-- Start of picture text -->map noise"
                    "<!-- End of picture text -->\nAfter"
                )
            )
            changed = ImageTextProcessor(context).process(segment)
            assert changed is True
            segment.update.assert_called_once_with("Before\n\nAfter")
            context.increment.assert_called_once_with("image_text_removed")

        If the markers are absent, ``segment.update`` and ``context.increment``
        are not called.
        """

        pattern = r"<!-- Start of picture text -->.*?" r"<!-- End of picture text -->"

        cleaned = re.sub(pattern, "", segment.current_text, flags=re.DOTALL)

        if cleaned != segment.current_text:

            segment.update(cleaned)

            self.context.increment("image_text_removed")

            return True

        return False
