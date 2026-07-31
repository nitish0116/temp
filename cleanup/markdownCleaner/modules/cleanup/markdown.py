from ..core.processor import SegmentProcessor
from .markup import normalize_inline_markup
from .protection import protect_markdown


class MarkdownProcessor(SegmentProcessor):
    """Normalize legacy inline HTML and whitespace in editable segments.

    This processor belongs to the older segment-oriented ``NovelCleanupStage``
    workflow. It converts literal ``<br>`` elements to newlines, removes simple
    ``<u>`` tags and HTML comments, and collapses horizontal whitespace. It does
    not parse or rebuild whole-document structure.

    Example::

        from unittest.mock import Mock

        context = Mock()
        segment = Mock(
            current_text="<u>Chapter 1</u><br><!-- note -->The   beginning."
        )
        assert MarkdownProcessor(context).process(segment) is True
        segment.update.assert_called_once_with("Chapter 1\nThe beginning.")
        context.increment.assert_called_once_with("markdown_cleaned")
    """

    name = "Markdown"

    def process(self, segment):
        """Clean supported inline markup and whitespace in one segment.

        Args:
            segment: Editable segment exposing ``current_text`` and ``update``.

        Workflow:
            1. Replace literal ``<br>`` elements with newline characters.
            2. Remove simple opening and closing underline tags.
            3. Remove HTML comments, including multiline comments.
            4. Collapse consecutive spaces and tabs to one space.

        Returns:
            ``True`` if cleaned text differs from the original; otherwise
            ``False``. A changed segment is updated once and increments the
            ``markdown_cleaned`` context statistic once.

        Example::

            from unittest.mock import Mock

            context = Mock()
            segment = Mock(current_text="<u>Title</u><br>Text\t  continues")
            changed = MarkdownProcessor(context).process(segment)
            assert changed is True
            segment.update.assert_called_once_with("Title\nText continues")
            context.increment.assert_called_once_with("markdown_cleaned")

        For ``segment.current_text == "Already clean."``, the method returns
        ``False`` and calls neither ``update`` nor ``increment``.
        """

        before = segment.current_text
        protected = protect_markdown(before)
        after = protected.restore(normalize_inline_markup(protected.text))

        if after == before:
            return False

        segment.update(after)
        self.record_change(
            segment=segment,
            before=before,
            after=segment.current_text,
            reason="Legacy inline Markdown/HTML cleanup",
        )
        self.context.increment("markdown_cleaned")
        return True
