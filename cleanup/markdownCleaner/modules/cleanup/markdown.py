import re


class MarkdownProcessor:
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

    def __init__(self, context):
        """Bind the processor to the shared processing context.

        Args:
            context: Active context whose ``increment`` method records the
                ``markdown_cleaned`` metric when a segment is modified.

        Example::

            context = object()
            processor = MarkdownProcessor(context)
            assert processor.context is context

        Construction stores the reference only; cleanup begins in ``process``.
        """

        self.context = context

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

        text = segment.current_text

        original = text

        # HTML line breaks

        text = text.replace("<br>", "\n")

        # underline tags

        text = re.sub(r"</?u>", "", text)

        # remove empty HTML comments

        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

        # normalize spaces

        text = re.sub(r"[ \t]+", " ", text)

        if text != original:

            segment.update(text)

            self.context.increment("markdown_cleaned")

            return True

        return False
